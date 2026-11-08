"""Guardrails da Etapa 2: conferem a decisão do modelo contra o que as ferramentas devolveram.

Funções puras. A severidade só sobe (criar_rascunho < pedir_esclarecimento < rejeitar), com
uma exceção explícita: uma rejeição sem código de erro do backend vira esclarecimento, porque
o backend não aceita registrar rejeição sem motivo verificável. Cada mudança gera um registro
em `acionamentos`.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from extrator.atributos import atributos, cobertura, confere, medida_no_nome, produto_coletivo
from extrator.guardrails_extracao import ItemVerificado
from extrator.ingestao import normalizar
from extrator.schemas import DecisaoAgente, DecisaoTipo, ItemDecidido
from pedidos.errors import CODIGOS_REJEICAO

SEVERIDADE = {"criar_rascunho": 0, "pedir_esclarecimento": 1, "rejeitar": 2}
LIMITE_QUANTIDADE = 100_000
GUARDRAIL_DO_BLOQUEIO = {"CODIGO_NAO_ANCORADO": "codigo_ancorado"}


def _mais_grave(*decisoes: DecisaoTipo) -> DecisaoTipo:
    return max(decisoes, key=SEVERIDADE.__getitem__)


def _curto(texto: str, limite: int = 500) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


@dataclass(frozen=True, slots=True)
class RegistroChamada:
    """Uma chamada de ferramenta feita pelo modelo nesta execução (proveniência)."""

    ferramenta: str
    alvo: int | Literal["cliente"] | None
    argumentos: dict[str, Any]
    resultado: dict[str, Any] | None = None
    erro: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ContextoResolucao:
    itens: list[ItemVerificado]
    cliente_codigo: str | None
    cliente_nome: str | None
    texto_referencia: str | None
    texto_verificavel: bool
    chamadas: list[RegistroChamada] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DecisaoItem:
    idx: int
    decisao: DecisaoTipo
    produto_id: str | None = None
    quantidade: int | None = None
    codigo: str | None = None
    mensagem: str | None = None
    por_descricao: bool = False


@dataclass
class ResultadoResolucao:
    decisao: DecisaoTipo
    cliente_id: str | None
    itens: list[DecisaoItem]
    carrinho: list[dict[str, Any]]
    perguntas: list[str]
    motivos: list[dict[str, Any]]
    acionamentos: list[dict[str, Any]]

    @property
    def casamento_por_descricao(self) -> bool:
        return any(i.por_descricao for i in self.itens if i.decisao == "criar_rascunho")

    @property
    def rebaixada(self) -> bool:
        return bool(self.acionamentos)


class _Proveniencia:
    """Índices sobre as chamadas registradas."""

    def __init__(self, chamadas: list[RegistroChamada]):
        self.candidatos: dict[int, dict[str, dict]] = defaultdict(dict)
        self.exatas: dict[int, list[tuple[str, str, list[dict]]]] = defaultdict(list)
        self.clientes: dict[str, dict] = {}
        for chamada in chamadas:
            if chamada.resultado is None:
                continue
            if chamada.ferramenta == "buscar_produtos" and isinstance(chamada.alvo, int):
                resultado = chamada.resultado
                for candidato in resultado.get("candidatos", []):
                    self.candidatos[chamada.alvo][str(candidato["produto_id"])] = candidato
                if resultado.get("modo") in {"ean", "sku"}:
                    consulta = str(resultado.get("consulta", ""))
                    self.exatas[chamada.alvo].append(
                        (resultado["modo"], consulta, list(resultado.get("candidatos", [])))
                    )
            elif chamada.ferramenta == "buscar_cliente":
                for cliente in chamada.resultado.get("clientes", []):
                    self.clientes[str(cliente["cliente_id"])] = cliente

    def busca_exata(self, idx: int, item: ItemVerificado) -> list[dict] | None:
        """Candidatos da busca exata pelo código do item, se o modelo a fez."""
        for modo, consulta, candidatos in self.exatas.get(idx, []):
            if modo == "ean" and item.ean and re.sub(r"[\s.-]", "", consulta) == item.ean:
                return candidatos
            if modo == "sku" and item.sku and consulta.strip().upper() == item.sku:
                return candidatos
        return None


def _estado_produto(produto: dict, quantidade: int | None) -> str | None:
    if not produto.get("ativo", False):
        return "PRODUTO_INATIVO"
    if produto.get("preco_unitario") is None:
        return "SEM_PRECO"
    if quantidade is not None and int(produto.get("estoque_disponivel", 0)) < quantidade:
        return "ESTOQUE_INSUFICIENTE"
    return None


_MENSAGENS_REJEICAO = {
    "PRODUTO_INEXISTENTE": "O código {c} do item “{d}” não existe no catálogo.",
    "PRODUTO_INATIVO": "O produto do item “{d}” está inativo e não pode ser vendido.",
    "SEM_PRECO": "O produto do item “{d}” está sem preço vigente.",
    "ESTOQUE_INSUFICIENTE": "Não há estoque suficiente para o item “{d}” ({q} unidades).",
    "QUANTIDADE_INVALIDA": "O item “{d}” tem quantidade inválida.",
    "CLIENTE_INVALIDO": "O cliente informado não está ativo no cadastro.",
    "CARRINHO_INVALIDO": "O carrinho do item “{d}” não pôde ser validado.",
}


def _rejeicao(item: ItemVerificado, codigo: str) -> DecisaoItem:
    modelo = _MENSAGENS_REJEICAO.get(codigo, "O item “{d}” não pode ser atendido.")
    mensagem = modelo.format(c=item.ean or item.sku or "", d=item.descricao, q=item.quantidade)
    return DecisaoItem(item.idx, "rejeitar", codigo=codigo, mensagem=_curto(mensagem))


def _esclarecimento(item: ItemVerificado, codigo: str, pergunta: str | None) -> DecisaoItem:
    texto = pergunta or f"Confirme qual produto deseja para o item “{item.descricao}”."
    return DecisaoItem(item.idx, "pedir_esclarecimento", codigo=codigo, mensagem=_curto(texto))


def _opcoes(produtos: list[dict]) -> str:
    vistos = dict.fromkeys(f"{p['nome']} ({p['marca']}, {p['embalagem']})" for p in produtos)
    return "; ".join(vistos)


class _Avaliador:
    def __init__(self, ctx: ContextoResolucao):
        self.ctx = ctx
        self.prov = _Proveniencia(ctx.chamadas)
        self.acionamentos: list[dict[str, Any]] = []

    def acionar(self, guardrail: str, idx: int | None, original: str, final: str) -> None:
        self.acionamentos.append(
            {
                "guardrail": guardrail,
                "item_idx": idx if idx is not None and 0 <= idx <= 99 else None,
                "decisao_original": original,
                "decisao_final": final,
            }
        )

    def _descricao(self, item: ItemVerificado, escolhido: dict) -> tuple[str, DecisaoItem] | None:
        """Guardrails 4 e 5: atributos conferidos e ambiguidade (itens sem código)."""
        texto = f"{item.descricao} {item.unidade_texto or ''}"
        pedido = atributos(texto)
        candidatos = self.prov.candidatos[item.idx]
        marcas = {str(c.get("marca") or "") for c in candidatos.values()}
        passam = [
            c for c in candidatos.values() if c.get("ativo") and confere(pedido, texto, c, marcas)
        ]
        ids = {str(c["produto_id"]) for c in passam}
        if str(escolhido["produto_id"]) not in ids:
            pergunta = (
                f"O produto encontrado ({escolhido['nome']}, {escolhido['embalagem']}) não "
                f"confere com “{item.descricao}”. Confirme marca, tamanho e embalagem."
            )
            return "atributos_conferidos", _esclarecimento(item, "ATRIBUTOS_DIVERGENTES", pergunta)

        def chave(c):
            return (cobertura(pedido, c), medida_no_nome(pedido, c))

        melhor = max(map(chave, passam))
        topo = [c for c in passam if chave(c) == melhor]
        if len(topo) >= 2 or str(escolhido["produto_id"]) not in {
            str(c["produto_id"]) for c in topo
        }:
            opcoes = _opcoes([*topo, escolhido])
            pergunta = (
                f"O item “{item.descricao}” corresponde a mais de um produto: {opcoes}. Qual deles?"
            )
            return "ambiguidade", _esclarecimento(item, "AMBIGUIDADE", pergunta)
        coletivos = [c for c in passam if produto_coletivo(c)]
        if pedido.coletiva_sem_contagem and coletivos and len(coletivos) < len(passam):
            opcoes = _opcoes(passam)
            pergunta = (
                f"Para “{item.descricao}” ({item.unidade_texto or 'caixas'}), confirme a "
                f"embalagem: {opcoes}."
            )
            return "ambiguidade", _esclarecimento(item, "AMBIGUIDADE_EMBALAGEM", pergunta)
        return None

    def avaliar(self, item: ItemVerificado, mi: ItemDecidido) -> tuple[str | None, DecisaoItem]:
        """Decisão final do item e o guardrail que a alterou (None se o modelo prevaleceu)."""
        codigo_item = item.ean or item.sku
        definitiva = None
        if codigo_item:
            exata = self.prov.busca_exata(item.idx, item)
            if exata is not None:
                definitiva = (
                    "PRODUTO_INEXISTENTE"
                    if not exata
                    else _estado_produto(exata[0], item.quantidade)
                )
        if mi.decisao == "rejeitar":
            codigo = definitiva or (mi.codigo if mi.codigo in CODIGOS_REJEICAO else None)
            if codigo:
                return None, _rejeicao(item, codigo)
            return "coerencia_decisao", _esclarecimento(item, "DECISAO_INCOERENTE", mi.pergunta)
        if definitiva:
            # Guardrail 3: código citado sem produto vendável só admite rejeição.
            return "proibido_substituir", _rejeicao(item, definitiva)
        if mi.decisao == "pedir_esclarecimento":
            return None, _esclarecimento(item, mi.codigo or "ESCLARECIMENTO", mi.pergunta)

        if mi.produto_id is None:
            return "coerencia_decisao", _esclarecimento(item, "ITEM_SEM_PRODUTO", mi.pergunta)
        candidatos = self.prov.candidatos.get(item.idx, {})
        escolhido = candidatos.get(str(mi.produto_id))
        if escolhido is None:
            # Guardrail 1: produto fabricado ou trazido da busca de outro item.
            return "proveniencia_item", _esclarecimento(item, "PRODUTO_SEM_PROVENIENCIA", None)
        if codigo_item:
            if (item.ean and escolhido.get("ean") != item.ean) or (
                item.sku and escolhido.get("sku") != item.sku
            ):
                pergunta = (
                    f"O produto selecionado não corresponde ao código {codigo_item} informado "
                    f"para “{item.descricao}”. Confirme o código."
                )
                return "codigo_exato", _esclarecimento(item, "PRODUTO_DIVERGENTE", pergunta)
        else:
            alterado = self._descricao(item, escolhido)
            if alterado:
                return alterado
        estado = _estado_produto(escolhido, item.quantidade)
        if estado:
            return "estado_produto", _rejeicao(item, estado)
        return None, DecisaoItem(
            item.idx,
            "criar_rascunho",
            produto_id=str(mi.produto_id),
            quantidade=item.quantidade,
            por_descricao=not codigo_item,
        )

    def cliente(self, decisao: DecisaoAgente) -> tuple[DecisaoTipo, str | None, str | None]:
        """Guardrail 6: (decisão, cliente_id, pergunta)."""
        ctx = self.ctx
        referencia = normalizar(ctx.texto_referencia or "")
        codigo = (ctx.cliente_codigo or "").strip().upper()
        if codigo and ctx.texto_verificavel and normalizar(codigo) not in referencia:
            codigo = ""  # código de cliente sem evidência no documento é ignorado
        escolhido = str(decisao.cliente_id) if decisao.cliente_id else None
        vistos = self.prov.clientes
        if codigo:
            aceitos = {i for i, c in vistos.items() if c["codigo"].upper() == codigo and c["ativo"]}
            if escolhido in aceitos:
                return "criar_rascunho", escolhido, None
            pergunta = (
                f"O cliente {codigo} não foi encontrado ativo no cadastro. Confirme o código "
                "ou solicite o cadastro à pessoa responsável."
            )
        elif ctx.cliente_nome:
            nome = normalizar(ctx.cliente_nome)
            aceitos = {i for i, c in vistos.items() if c["ativo"] and normalizar(c["nome"]) == nome}
            if len(aceitos) == 1 and escolhido in aceitos:
                return "criar_rascunho", escolhido, None
            pergunta = (
                f"Não foi possível identificar com segurança o cliente “{ctx.cliente_nome}”. "
                "Informe o código do cliente."
            )
        else:
            pergunta = "O documento não identifica o cliente. Informe o código do cliente."
        return "pedir_esclarecimento", None, _curto(decisao.cliente_pergunta or pergunta)


def aplicar_guardrails(decisao: DecisaoAgente | None, ctx: ContextoResolucao) -> ResultadoResolucao:
    """Decisão final. `decisao=None` quando a Etapa 2 não rodou (todos os itens bloqueados)."""
    avaliador = _Avaliador(ctx)
    original_geral = decisao.decisao if decisao else None
    por_idx: dict[int, ItemDecidido] = {}
    if decisao:
        validos = {item.idx for item in ctx.itens}
        for mi in decisao.itens:
            # Guardrail 7: sem itens extras nem duplicados.
            if mi.item_extraido_idx not in validos or mi.item_extraido_idx in por_idx:
                avaliador.acionar("item_extra", mi.item_extraido_idx, mi.decisao, mi.decisao)
                continue
            por_idx[mi.item_extraido_idx] = mi

    finais: list[DecisaoItem] = []
    for item in ctx.itens:
        mi = por_idx.get(item.idx)
        original = mi.decisao if mi else (original_geral or "pedir_esclarecimento")
        if item.bloqueio:
            # Bloqueios da Etapa 1 (quantidade, código não ancorado) prevalecem.
            b = item.bloqueio
            final = DecisaoItem(item.idx, b.decisao, codigo=b.codigo, mensagem=_curto(b.mensagem))
            if mi and mi.decisao != b.decisao:
                guardrail = GUARDRAIL_DO_BLOQUEIO.get(b.codigo, "quantidade_parser")
                avaliador.acionar(guardrail, item.idx, mi.decisao, b.decisao)
        elif mi is None:
            # Guardrail 8: cobertura.
            final = _esclarecimento(item, "ITEM_NAO_RESOLVIDO", None)
            if decisao:
                avaliador.acionar("cobertura", item.idx, original, final.decisao)
        else:
            guardrail, final = avaliador.avaliar(item, mi)
            if guardrail:
                avaliador.acionar(guardrail, item.idx, mi.decisao, final.decisao)
        finais.append(final)

    cliente_decisao, cliente_id, cliente_pergunta = "criar_rascunho", None, None
    if decisao:
        cliente_decisao, cliente_id, cliente_pergunta = avaliador.cliente(decisao)
        if cliente_decisao != "criar_rascunho":
            avaliador.acionar("cliente", None, original_geral, cliente_decisao)

    severidades = [f.decisao for f in finais] or ["pedir_esclarecimento"]
    geral = _mais_grave(
        *severidades, cliente_decisao, *([original_geral] if original_geral else [])
    )
    perguntas: list[str] = []
    motivos = [
        {"codigo": f.codigo, "mensagem": f.mensagem, "item_idx": f.idx}
        for f in finais
        if f.decisao != "criar_rascunho" and f.codigo and f.mensagem
    ]

    # Guardrail 9: consolidação de produtos repetidos e limite por item.
    carrinho: dict[str, int] = {}
    for f in finais:
        if f.decisao == "criar_rascunho" and f.produto_id and f.quantidade:
            carrinho[f.produto_id] = carrinho.get(f.produto_id, 0) + f.quantidade
    if any(q > LIMITE_QUANTIDADE for q in carrinho.values()):
        avaliador.acionar("quantidades", None, geral, "pedir_esclarecimento")
        geral = _mais_grave(geral, "pedir_esclarecimento")
        perguntas.append("A soma das quantidades de um mesmo produto excede o limite por item.")

    # Guardrail 10: coerência da decisão geral.
    if geral == "rejeitar" and not any(m["codigo"] in CODIGOS_REJEICAO for m in motivos):
        avaliador.acionar("coerencia_decisao", None, "rejeitar", "pedir_esclarecimento")
        geral = "pedir_esclarecimento"
    if geral == "criar_rascunho" and not carrinho:
        avaliador.acionar("coerencia_decisao", None, "criar_rascunho", "pedir_esclarecimento")
        geral = "pedir_esclarecimento"
    if geral == "pedir_esclarecimento":
        perguntas = [
            *(f.mensagem for f in finais if f.decisao == "pedir_esclarecimento" and f.mensagem),
            *([cliente_pergunta] if cliente_pergunta else []),
            *perguntas,
            *(decisao.perguntas if decisao else []),
        ]
        if not perguntas:
            perguntas = (
                ["Não identificamos itens no documento. Envie a lista de produtos e quantidades."]
                if not ctx.itens
                else ["Confirme os itens e o cliente do pedido para prosseguirmos."]
            )
        perguntas = [_curto(p) for p in dict.fromkeys(perguntas)][:50]
    else:
        perguntas = []
    if (
        original_geral
        and geral != original_geral
        and not any(
            a["item_idx"] is None and a["decisao_final"] == geral for a in avaliador.acionamentos
        )
    ):
        avaliador.acionar("coerencia_decisao", None, original_geral, geral)

    return ResultadoResolucao(
        decisao=geral,
        cliente_id=cliente_id,
        itens=finais,
        carrinho=[
            {"produto_id": produto_id, "quantidade": quantidade, "unidade": "UN"}
            for produto_id, quantidade in carrinho.items()
        ]
        if geral == "criar_rascunho"
        else [],
        perguntas=perguntas,
        motivos=motivos[:100],
        acionamentos=avaliador.acionamentos[:200],
    )


def rebaixar_por_revalidacao(
    resultado: ResultadoResolucao, problemas: list[dict[str, Any]]
) -> ResultadoResolucao:
    """Guardrail 11: o backend recusou o carrinho; a decisão vira rejeição com seus códigos."""
    por_produto = {i.produto_id: i.idx for i in resultado.itens if i.produto_id}
    motivos = [
        {
            "codigo": p["codigo"] if p.get("codigo") in CODIGOS_REJEICAO else "CARRINHO_INVALIDO",
            "mensagem": _curto(str(p.get("mensagem") or "Carrinho recusado pelo backend.")),
            "item_idx": por_produto.get(str(p.get("produto_id"))) if p.get("produto_id") else None,
        }
        for p in problemas
    ] or [
        {
            "codigo": "CARRINHO_INVALIDO",
            "mensagem": "Carrinho recusado pelo backend.",
            "item_idx": None,
        }
    ]
    acionamento = {
        "guardrail": "revalidacao_carrinho",
        "item_idx": None,
        "decisao_original": resultado.decisao,
        "decisao_final": "rejeitar",
    }
    return ResultadoResolucao(
        decisao="rejeitar",
        cliente_id=resultado.cliente_id,
        itens=resultado.itens,
        carrinho=[],
        perguntas=[],
        motivos=(resultado.motivos + motivos)[:100],
        acionamentos=(resultado.acionamentos + [acionamento])[:200],
    )
