"""Guardrails da Etapa 1: ancoram a extração do modelo no documento.

Funções puras e determinísticas. Elas só restringem: descartam itens sem evidência, anulam
códigos não ancorados, bloqueiam quantidades não inteiras e marcam revisão. Nunca
acrescentam itens, códigos ou quantidades que o modelo não tenha extraído.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from extrator.ingestao import normalizar
from extrator.quantidade import Situacao, parsear_quantidade
from extrator.schemas import ExtracaoPedido, ItemExtraido

DecisaoBloqueio = Literal["pedir_esclarecimento", "rejeitar"]

_PALAVRAS_SEM_CONTEUDO = frozenset(
    "ean sku cod codigo produto item ref referencia de do da dos das o a os as e "
    "un und unid unidade unidades qtd quantidade".split()
)
_DIGITOS = re.compile(r"\d+")
_CODIGO_COM_SEPARADORES = re.compile(r"(?<![\w])\d[\d .-]{6,24}\d(?![\w])")


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bloqueio(_Modelo):
    decisao: DecisaoBloqueio
    codigo: str
    mensagem: str


class ItemVerificado(_Modelo):
    """Item que segue para a resolução, com códigos e quantidade já verificados."""

    idx: int
    descricao: str
    ean: str | None
    sku: str | None
    quantidade: int | None
    quantidade_texto: str | None
    situacao_quantidade: Situacao
    unidade_texto: str | None
    evidencia: str
    verificavel: bool
    bloqueio: Bloqueio | None = None


@dataclass(frozen=True, slots=True)
class Acionamento:
    guardrail: str
    item_idx: int | None
    efeito: Literal[
        "descartado", "codigo_anulado", "esclarecimento", "rejeicao", "revisao", "sinal"
    ]


@dataclass
class ResultadoVerificacao:
    itens: list[ItemVerificado]
    descartados: list[int] = field(default_factory=list)
    acionamentos: list[Acionamento] = field(default_factory=list)
    motivos_revisao: list[str] = field(default_factory=list)

    def revisar(self, motivo: str) -> None:
        if motivo not in self.motivos_revisao:
            self.motivos_revisao.append(motivo)

    def registro(self, extracao: ExtracaoPedido) -> dict:
        """Resumo para `AnaliseInput.extracao`: itens e cabeçalho, nunca o texto integral."""
        return {
            "cliente_codigo": extracao.cliente_codigo,
            "cliente_nome": extracao.cliente_nome,
            "referencia": extracao.referencia,
            "itens": [item.model_dump(mode="json") for item in self.itens],
            "descartados": self.descartados,
            "observacoes": extracao.observacoes,
            "sinais_de_instrucao": extracao.sinais_de_instrucao,
        }


def ean13_valido(codigo: str) -> bool:
    if len(codigo) != 13 or not codigo.isdigit():
        return False
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(codigo))
    return soma % 10 == 0


def codigos_numericos(texto_normalizado: str) -> set[str]:
    """Sequências de dígitos do texto, isoladas e com separadores ("2990 0000 00712")."""
    codigos = set(_DIGITOS.findall(texto_normalizado))
    for trecho in _CODIGO_COM_SEPARADORES.findall(texto_normalizado):
        codigos.add(re.sub(r"\D", "", trecho))
    return codigos


def _contem_termo(texto_normalizado: str, termo: str) -> bool:
    padrao = r"(?<![\w])" + re.escape(normalizar(termo)) + r"(?![\w])"
    return re.search(padrao, texto_normalizado) is not None


def _descricao_util(item: ItemExtraido) -> bool:
    """Sobra descrição para buscar o produto depois de remover códigos e palavras vazias?"""
    texto = normalizar(item.descricao)
    for codigo in (item.ean, item.sku):
        if codigo:
            texto = texto.replace(normalizar(codigo), " ")
    palavras = [p for p in re.findall(r"[a-z]{2,}", texto) if p not in _PALAVRAS_SEM_CONTEUDO]
    return sum(len(p) for p in palavras) >= 3


_BLOQUEIOS_QUANTIDADE: dict[Situacao, tuple[DecisaoBloqueio, str, str]] = {
    Situacao.FRACAO: (
        "pedir_esclarecimento",
        "QUANTIDADE_FRACIONARIA",
        "O item “{d}” foi pedido em quantidade fracionária ({q}). Cada unidade é uma "
        "embalagem inteira: quantas embalagens devem ser consideradas?",
    ),
    Situacao.AUSENTE: (
        "pedir_esclarecimento",
        "QUANTIDADE_AUSENTE",
        "Qual a quantidade desejada do item “{d}”?",
    ),
    Situacao.AMBIGUA: (
        "pedir_esclarecimento",
        "QUANTIDADE_AMBIGUA",
        "A quantidade do item “{d}” está ambígua ({q}). Quantas embalagens devem ser consideradas?",
    ),
    Situacao.FORA_DO_LIMITE: (
        "pedir_esclarecimento",
        "QUANTIDADE_ACIMA_LIMITE",
        "A quantidade do item “{d}” ({q}) excede o limite por item. Confirme a quantidade.",
    ),
    Situacao.ZERO_OU_NEGATIVO: (
        "rejeitar",
        "QUANTIDADE_INVALIDA",
        "O item “{d}” tem quantidade zero ou negativa ({q}); um pedido exige quantidade positiva.",
    ),
}


def _mais_grave(atual: Bloqueio | None, novo: Bloqueio) -> Bloqueio:
    if atual is None or (novo.decisao == "rejeitar" and atual.decisao != "rejeitar"):
        return novo
    return atual


def verificar_extracao(
    extracao: ExtracaoPedido, texto_referencia: str | None, texto_verificavel: bool
) -> ResultadoVerificacao:
    resultado = ResultadoVerificacao(itens=[])
    verificavel = texto_verificavel and bool(texto_referencia)
    referencia = normalizar(texto_referencia or "")
    codigos_documento = codigos_numericos(referencia)

    if not verificavel:
        resultado.revisar("texto_nao_verificavel")

    for idx, item in enumerate(extracao.itens):
        evidencia = normalizar(item.evidencia)
        # 1. Evidência literal: item sem trecho no documento é tratado como inventado.
        if verificavel and (not evidencia or evidencia not in referencia):
            resultado.descartados.append(idx)
            resultado.acionamentos.append(Acionamento("evidencia_literal", idx, "descartado"))
            resultado.revisar("extracao_suspeita")
            continue

        bloqueio: Bloqueio | None = None
        # 2. Códigos ancorados na evidência e, com texto verificável, no documento.
        ean = None
        if item.ean:
            candidato = re.sub(r"\D", "", normalizar(item.ean))
            ancorado = (
                ean13_valido(candidato)
                and candidato in codigos_numericos(evidencia)
                and (not verificavel or candidato in codigos_documento)
            )
            if ancorado:
                ean = candidato
            else:
                resultado.acionamentos.append(Acionamento("ean_ancorado", idx, "codigo_anulado"))
        sku = None
        if item.sku:
            ancorado = _contem_termo(evidencia, item.sku) and (
                not verificavel or _contem_termo(referencia, item.sku)
            )
            if ancorado:
                sku = item.sku.strip().upper()
            else:
                resultado.acionamentos.append(Acionamento("sku_ancorado", idx, "codigo_anulado"))
        codigo_anulado = (item.ean and not ean) or (item.sku and not sku)
        if codigo_anulado and not ean and not sku and not _descricao_util(item):
            bloqueio = Bloqueio(
                decisao="pedir_esclarecimento",
                codigo="CODIGO_NAO_ANCORADO",
                mensagem=(
                    f"Não foi possível confirmar o código do item “{item.descricao}” no "
                    "documento. Informe o código ou a descrição completa do produto."
                ),
            )
            resultado.acionamentos.append(Acionamento("codigo_nao_ancorado", idx, "esclarecimento"))

        # 3. Quantidade calculada pelo código; o valor do modelo é só sinal de qualidade.
        quantidade = parsear_quantidade(item.quantidade_texto)
        if item.quantidade != quantidade.valor:
            resultado.acionamentos.append(Acionamento("divergencia_quantidade", idx, "sinal"))
        if quantidade.situacao in _BLOQUEIOS_QUANTIDADE:
            decisao, codigo, modelo = _BLOQUEIOS_QUANTIDADE[quantidade.situacao]
            texto = modelo.format(d=item.descricao, q=item.quantidade_texto or "não informada")
            bloqueio = _mais_grave(
                bloqueio, Bloqueio(decisao=decisao, codigo=codigo, mensagem=texto)
            )
            efeito = "rejeicao" if decisao == "rejeitar" else "esclarecimento"
            resultado.acionamentos.append(Acionamento("quantidade_parser", idx, efeito))

        resultado.itens.append(
            ItemVerificado(
                idx=idx,
                descricao=item.descricao,
                ean=ean,
                sku=sku,
                quantidade=quantidade.valor,
                quantidade_texto=item.quantidade_texto,
                situacao_quantidade=quantidade.situacao,
                unidade_texto=item.unidade_texto,
                evidencia=item.evidencia,
                verificavel=verificavel,
                bloqueio=bloqueio,
            )
        )

    # 4. Sanidade: nenhum item num documento com números, ou mais itens que linhas numéricas.
    if verificavel:
        linhas_com_numero = sum(
            1 for linha in (texto_referencia or "").splitlines() if re.search(r"\d", linha)
        )
        if (not extracao.itens and linhas_com_numero) or len(extracao.itens) > linhas_com_numero:
            resultado.acionamentos.append(Acionamento("sanidade_extracao", None, "revisao"))
            resultado.revisar("extracao_suspeita")

    # 5. Instruções embutidas: o pedido segue, a instrução nunca é seguida.
    if extracao.sinais_de_instrucao:
        resultado.acionamentos.append(Acionamento("sinais_de_instrucao", None, "revisao"))
        resultado.revisar("possivel_injecao")
    return resultado
