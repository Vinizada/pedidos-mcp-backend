"""Leitura determinística da quantidade escrita no documento.

O valor que o modelo devolve em `quantidade` nunca é usado: vale o que este parser obtém de
`quantidade_texto`. Na dúvida, o resultado não é um inteiro e o item vai para esclarecimento.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

LIMITE_QUANTIDADE = 100_000

_UNIDADES = {
    "zero": 0,
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "onze": 11,
    "doze": 12,
    "treze": 13,
    "catorze": 14,
    "quatorze": 14,
    "quinze": 15,
    "dezesseis": 16,
    "dezessete": 17,
    "dezoito": 18,
    "dezenove": 19,
    "vinte": 20,
    "trinta": 30,
    "quarenta": 40,
    "cinquenta": 50,
    "sessenta": 60,
    "setenta": 70,
    "oitenta": 80,
    "noventa": 90,
    "cem": 100,
    "cento": 100,
    "duzentos": 200,
    "duzentas": 200,
    "trezentos": 300,
    "trezentas": 300,
    "quinhentos": 500,
    "quinhentas": 500,
}
_MULTIPLICADORES = {
    "duzia": 12,
    "duzias": 12,
    "dezena": 10,
    "dezenas": 10,
    "centena": 100,
    "mil": 1000,
}
_FRACOES = {"meio", "meia", "metade", "terco", "quarto"}
_NEGATIVOS = {"menos", "negativo", "negativa"}
_NENHUM = {"nenhum", "nenhuma"}
_VAGOS = {"alguns", "algumas", "uns", "umas", "varios", "varias", "pouco", "poucos", "muitos"}

_NUMERO = re.compile(r"(?<![\w.,])([-−]\s*)?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?:\.(\d+))?")


class Situacao(StrEnum):
    INTEIRO = "inteiro"
    FRACAO = "fracao"
    ZERO_OU_NEGATIVO = "zero_ou_negativo"
    AUSENTE = "ausente"
    AMBIGUA = "ambigua"
    FORA_DO_LIMITE = "fora_do_limite"


@dataclass(frozen=True, slots=True)
class Quantidade:
    situacao: Situacao
    valor: int | None = None


def _sem_acentos(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def _numeros_digitados(texto: str) -> tuple[list[float], bool]:
    """Valores numéricos escritos com dígitos e se algum deles é negativo."""
    valores: list[float] = []
    negativo = False
    for sinal, inteiro, decimal_virgula, decimal_ponto in _NUMERO.findall(texto):
        base = int(inteiro.replace(".", ""))
        decimal = decimal_virgula or decimal_ponto
        valor = base + (int(decimal) / 10 ** len(decimal) if decimal and int(decimal) else 0)
        if sinal:
            negativo = True
            valor = -valor
        valores.append(valor)
    return valores, negativo


def _numero_por_extenso(palavras: list[str]) -> tuple[int | None, bool]:
    """Soma "vinte e cinco", multiplica "duas dúzias"; indica fração ("meia")."""
    total = 0
    encontrou = False
    fracao = False
    for indice, palavra in enumerate(palavras):
        if palavra in _UNIDADES:
            total += _UNIDADES[palavra]
            encontrou = True
        elif palavra in _MULTIPLICADORES:
            anterior = palavras[indice - 1] if indice else ""
            if anterior in {"meia", "meio"}:
                total += _MULTIPLICADORES[palavra] // 2
                fracao = False
            else:
                total = (total or 1) * _MULTIPLICADORES[palavra]
            encontrou = True
        elif palavra in _FRACOES:
            proxima = palavras[indice + 1] if indice + 1 < len(palavras) else ""
            if proxima not in _MULTIPLICADORES:
                fracao = True
    return (total if encontrou else None), fracao


def parsear_quantidade(texto: str | None) -> Quantidade:
    if texto is None or not texto.strip():
        return Quantidade(Situacao.AUSENTE)
    normalizado = _sem_acentos(texto)
    palavras = re.findall(r"[a-z]+", normalizado)
    digitados, negativo_digitado = _numeros_digitados(normalizado)
    extenso, fracao_extenso = _numero_por_extenso(palavras)

    if negativo_digitado or _NEGATIVOS & set(palavras):
        return Quantidade(Situacao.ZERO_OU_NEGATIVO)
    if _NENHUM & set(palavras):
        return Quantidade(Situacao.ZERO_OU_NEGATIVO)
    if fracao_extenso or any(v != int(v) for v in digitados):
        return Quantidade(Situacao.FRACAO)
    candidatos = {int(v) for v in digitados}
    if extenso is not None:
        candidatos.add(extenso)
    if not candidatos:
        # "algumas caixas", "o de sempre": sem número, a quantidade não foi informada.
        return Quantidade(Situacao.AUSENTE)
    if len(candidatos) > 1 or len(digitados) > 1:
        # "2 caixas de 12": mais de um número para um item é ambíguo.
        return Quantidade(Situacao.AMBIGUA)
    if _VAGOS & set(palavras):
        return Quantidade(Situacao.AMBIGUA)
    valor = candidatos.pop()
    if valor <= 0:
        return Quantidade(Situacao.ZERO_OU_NEGATIVO)
    if valor > LIMITE_QUANTIDADE:
        return Quantidade(Situacao.FORA_DO_LIMITE)
    return Quantidade(Situacao.INTEIRO, valor)
