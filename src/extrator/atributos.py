"""Atributos comparáveis entre a descrição do cliente e um produto do catálogo.

Funções puras usadas pelo guardrail de atributos e pela detecção de ambiguidade:
medidas (convertidas para g/ml), contagens de embalagem ("caixa com 12"), palavras de
conteúdo e marcas.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from extrator.ingestao import normalizar

Medida = tuple[int, str]  # (valor na unidade base, "g" | "ml")

_FATOR = {
    "kg": (1000, "g"),
    "quilo": (1000, "g"),
    "quilos": (1000, "g"),
    "g": (1, "g"),
    "gr": (1, "g"),
    "grama": (1, "g"),
    "gramas": (1, "g"),
    "l": (1000, "ml"),
    "lt": (1000, "ml"),
    "litro": (1000, "ml"),
    "litros": (1000, "ml"),
    "ml": (1, "ml"),
}
_UNIDADES = r"kg|quilos?|gramas?|gr|g|ml|litros?|lt|l"
_UNIDADE = r"(" + _UNIDADES + r")"
_MEDIDA_NUMERICA = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*" + _UNIDADE + r"(?![a-z])")
_MEDIDA_EXTENSO = re.compile(r"(?<![a-z])(meio|meia|um|uma|1/2)\s+(quilo|kg|litro|l)(?![a-z])")
_CONTAGEM = re.compile(
    r"(?<![a-z])(?:cx|caixa|fardo|pacote|pct|pack|embalagem|engradado)s?\s*"
    r"(?:fechad[ao]s?\s*)?(?:com|c/|de)\s*(\d+)(?![\d.,])(?!\s*(?:" + _UNIDADES + r")(?![a-z]))"
)
_EMBALAGEM_COLETIVA = re.compile(r"(?<![a-z])(caixas?|cx|fardos?|engradados?|packs?)(?![a-z])")

_NAO_CONTEUDO = frozenset(
    """
    de do da dos das com sem para por e o a os as um uma uns umas no na em ao
    pacote pacotes pct caixa caixas cx caixinha caixinhas garrafa garrafas pote potes frasco
    frascos lata latas vidro vidros unidade unidades un und fardo fardos embalagem
    fechada fechado kg g gr ml lt litro litros quilo quilos grama gramas meio meia tipo
    """.split()
)


@dataclass(frozen=True, slots=True)
class Atributos:
    medidas: frozenset[Medida]
    contagens: frozenset[int]
    palavras: tuple[str, ...]
    coletiva_sem_contagem: bool


def _medidas(texto: str) -> set[Medida]:
    medidas: set[Medida] = set()
    for valor, unidade in _MEDIDA_NUMERICA.findall(texto):
        fator, base = _FATOR[unidade]
        quantidade = Decimal(valor.replace(",", ".")) * fator
        if quantidade == quantidade.to_integral_value():
            medidas.add((int(quantidade), base))
    for quanto, unidade in _MEDIDA_EXTENSO.findall(texto):
        fator, base = _FATOR[unidade]
        medidas.add(((fator // 2) if quanto in {"meio", "meia", "1/2"} else fator, base))
    return medidas


def _palavras(texto: str) -> list[str]:
    sem_medidas = _MEDIDA_EXTENSO.sub(" ", _MEDIDA_NUMERICA.sub(" ", texto))
    return [p for p in re.findall(r"[a-z]{3,}", sem_medidas) if p not in _NAO_CONTEUDO]


def atributos(texto: str) -> Atributos:
    normalizado = normalizar(texto)
    contagens = {int(n) for n in _CONTAGEM.findall(normalizado)}
    return Atributos(
        medidas=frozenset(_medidas(normalizado)),
        contagens=frozenset(contagens),
        palavras=tuple(dict.fromkeys(_palavras(normalizado))),
        coletiva_sem_contagem=bool(_EMBALAGEM_COLETIVA.search(normalizado)) and not contagens,
    )


def texto_produto(produto: dict[str, Any]) -> str:
    partes = ("nome", "marca", "embalagem", "categoria")
    return " ".join(str(produto.get(campo) or "") for campo in partes)


def produto_coletivo(produto: dict[str, Any]) -> bool:
    """Embalagem fechada com várias unidades (ex.: caixa com 12 caixinhas)."""
    return bool(atributos(str(produto.get("embalagem") or "")).contagens) or "fechad" in normalizar(
        str(produto.get("embalagem") or "")
    )


def _palavra_coberta(palavra: str, tokens: set[str]) -> bool:
    # Prefixo de até 5 letras tolera plural/gênero ("recheada"/"recheado", "integrais").
    raiz = palavra[:5]
    return any(t.startswith(raiz) or palavra.startswith(t[:5]) for t in tokens if len(t) >= 3)


def cobertura(descricao: Atributos, produto: dict[str, Any]) -> int:
    tokens = set(re.findall(r"[a-z]{3,}", normalizar(texto_produto(produto))))
    return sum(_palavra_coberta(p, tokens) for p in descricao.palavras)


def marcas_citadas(texto: str, marcas: set[str]) -> set[str]:
    """Marcas do catálogo (dentre as candidatas) que aparecem por inteiro no texto."""
    normalizado = normalizar(texto)
    return {
        marca
        for marca in marcas
        if marca
        and re.search(r"(?<![\w])" + re.escape(normalizar(marca)) + r"(?![\w])", normalizado)
    }


def confere(
    descricao: Atributos, texto_cliente: str, produto: dict[str, Any], marcas: set[str]
) -> bool:
    """Medidas, contagens e marca citadas pelo cliente aparecem no produto?"""
    atributos_produto = atributos(texto_produto(produto))
    if not descricao.medidas <= atributos_produto.medidas:
        return False
    if not descricao.contagens <= atributos_produto.contagens:
        return False
    citadas = marcas_citadas(texto_cliente, marcas)
    return not citadas or str(produto.get("marca") or "") in citadas


def medida_no_nome(descricao: Atributos, produto: dict[str, Any]) -> bool:
    """A medida pedida é a do produto (nome), e não a de uma subunidade da embalagem?"""
    if not descricao.medidas:
        return True
    return descricao.medidas <= atributos(str(produto.get("nome") or "")).medidas
