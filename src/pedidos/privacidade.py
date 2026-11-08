"""Mascaramento de dados pessoais em textos livres gravados a partir de documentos."""

import re
from typing import Any

# Delimitadores (?<!\d)/(?!\d) evitam mascarar trechos de EANs de 13 dígitos ou preços.
_PADROES = (
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("[CNPJ]", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")),
    ("[CPF]", re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")),
    (
        "[TELEFONE]",
        re.compile(r"(?:\+55\s?)?(?<!\d)\(?\d{2}\)?[\s-]?9?\d{4}[\s-]?\d{4}(?!\d)"),
    ),
)


def mascarar_pii(texto: str) -> str:
    """Substitui e-mail, CNPJ, CPF e telefone por marcadores fixos."""
    for marcador, padrao in _PADROES:
        texto = padrao.sub(marcador, texto)
    return texto


def mascarar_pii_json(valor: Any) -> Any:
    """Aplica `mascarar_pii` a todas as strings de uma estrutura JSON."""
    if isinstance(valor, str):
        return mascarar_pii(valor)
    if isinstance(valor, list):
        return [mascarar_pii_json(item) for item in valor]
    if isinstance(valor, dict):
        return {chave: mascarar_pii_json(item) for chave, item in valor.items()}
    return valor
