"""Apoio aos testes do agente: catálogo como o backend devolve e chamadas registradas."""

from extrator.guardrails_extracao import Bloqueio, ItemVerificado
from extrator.guardrails_resolucao import RegistroChamada
from extrator.quantidade import Situacao
from pedidos.fixtures import catalog_records, client_records
from pedidos.seed import stable_id

CATALOGO = {p["sku"]: p for p in catalog_records()}
CLIENTES = {c["codigo"]: c for c in client_records()}


def produto(sku: str, **overrides) -> dict:
    """Candidato no formato de `buscar_produtos` para um produto real do seed."""
    p = CATALOGO[sku]
    candidato = {
        "produto_id": str(stable_id("produto", sku)),
        "sku": sku,
        "ean": p["ean"],
        "nome": p["nome"],
        "marca": p["marca"],
        "categoria": p["categoria"],
        "unidade": "UN",
        "embalagem": p["embalagem"],
        "ativo": p.get("ativo", True),
        "preco_unitario": str(p["preco"]),
        "estoque_disponivel": p["quantidade"],
        "score": 1.0,
        "origem": "lexical",
    }
    return candidato | overrides


def cliente(codigo: str, **overrides) -> dict:
    c = CLIENTES[codigo]
    dados = {
        "cliente_id": str(stable_id("cliente", codigo)),
        "codigo": codigo,
        "nome": c["nome"],
        "ativo": c.get("ativo", True),
    }
    return dados | overrides


def item(idx=0, descricao="café", *, ean=None, sku=None, quantidade=2, unidade=None, bloqueio=None):
    return ItemVerificado(
        idx=idx,
        descricao=descricao,
        ean=ean,
        sku=sku,
        quantidade=quantidade,
        quantidade_texto=str(quantidade) if quantidade else None,
        situacao_quantidade=Situacao.INTEIRO if quantidade else Situacao.AUSENTE,
        unidade_texto=unidade,
        evidencia=descricao,
        verificavel=True,
        bloqueio=bloqueio,
    )


def bloqueio(decisao="pedir_esclarecimento", codigo="QUANTIDADE_FRACIONARIA", mensagem="Quantas?"):
    return Bloqueio(decisao=decisao, codigo=codigo, mensagem=mensagem)


def busca(idx: int, consulta: str, candidatos: list[dict], modo="lexical") -> RegistroChamada:
    return RegistroChamada(
        "buscar_produtos",
        idx,
        {"entrada": {"consulta": consulta}},
        resultado={"consulta": consulta, "modo": modo, "candidatos": candidatos},
    )


def busca_cliente(consulta: str, clientes: list[dict]) -> RegistroChamada:
    return RegistroChamada(
        "buscar_cliente", "cliente", {"entrada": {"consulta": consulta}}, {"clientes": clientes}
    )
