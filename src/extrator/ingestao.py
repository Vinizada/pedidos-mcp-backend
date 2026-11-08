"""Prepara o documento original como blocos de conteúdo para o modelo.

Sem OCR: PDF e imagem vão nativos ao modelo multimodal. O texto extraído do PDF (quando há
camada de texto) serve apenas de referência para os guardrails de ancoragem.
"""

import base64
import re
import unicodedata
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from extrator.config import AgenteSettings
from pedidos.privacidade import mascarar_pii

# Página com menos caracteres úteis que isso é tratada como imagem (scan/foto).
MIN_CARACTERES_UTEIS_PAGINA = 30
# Limite da API para imagem em base64 (5 MB); o backend aceita uploads de até 10 MiB.
MAX_BYTES_IMAGEM = 5 * 1024 * 1024

# Tags que delimitam o documento ou imitam marcação de sistema/conversa.
TAGS_PROTEGIDAS = (
    "documento",
    "document",
    "system",
    "sistema",
    "instrucoes",
    "instruções",
    "instrucao",
    "instrução",
    "instructions",
    "prompt",
    "assistant",
    "assistente",
    "user",
    "usuario",
    "usuário",
    "human",
    "tool",
    "tool_use",
    "tool_result",
    "function_calls",
    "invoke",
    "parameter",
)
_TAG = re.compile(
    r"<\s*/?\s*(?:" + "|".join(TAGS_PROTEGIDAS) + r")(?![\w])[^<>]{0,200}>",
    re.IGNORECASE,
)
_TRACOS = str.maketrans({c: "-" for c in "‐‑‒–—―−"})
_ASPAS = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'"})
# Sequências com cara de código (EAN/SKU numérico): ≥ 8 caracteres entre dígitos e as
# letras confundíveis o/i/l, com pelo menos 6 dígitos. Fora delas o texto não é alterado.
_CODIGO = re.compile(r"(?<![\w])[0-9oil]{8,}(?![\w])")


@dataclass(frozen=True, slots=True)
class DocumentoPreparado:
    content_type: str
    blocos: list[dict[str, Any]]
    texto_referencia: str | None
    texto_verificavel: bool
    paginas: int | None = None
    paginas_sem_texto: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DocumentoRecusado:
    """Documento fora dos limites: vira `pedir_esclarecimento` sem chamar o modelo."""

    content_type: str
    codigo: str
    pergunta: str
    paginas: int | None = None


@dataclass(frozen=True, slots=True)
class LimitesIngestao:
    max_paginas: int = 10
    max_caracteres_txt: int = 50_000
    mascarar_pii_txt: bool = False

    @classmethod
    def de_settings(cls, settings: AgenteSettings) -> "LimitesIngestao":
        return cls(
            max_paginas=settings.agente_max_paginas,
            max_caracteres_txt=settings.agente_max_caracteres_txt,
            mascarar_pii_txt=settings.agente_mascarar_pii_txt,
        )


def neutralizar_delimitadores(texto: str) -> str:
    """Torna inertes tags que fechariam o bloco do documento ou imitariam o sistema."""
    return _TAG.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), texto)


def _codigo(match: re.Match) -> str:
    trecho = match.group(0)
    if sum(c.isdigit() for c in trecho) < 6:
        return trecho
    return trecho.replace("o", "0").replace("i", "1").replace("l", "1")


def normalizar(texto: str) -> str:
    """Forma canônica para comparar trechos do modelo com o documento.

    Minúsculas, sem acentos, traços e aspas tipográficos unificados, espaços colapsados e
    O→0 / I,l→1 apenas dentro de sequências que parecem códigos numéricos.
    """
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    texto = sem_acentos.translate(_TRACOS).translate(_ASPAS).lower()
    texto = _CODIGO.sub(_codigo, texto)
    return re.sub(r"\s+", " ", texto).strip()


def caracteres_uteis(texto: str) -> int:
    return sum(c.isalnum() for c in texto)


def _bloco_documento(texto: str) -> dict[str, Any]:
    return {
        "type": "text",
        "text": f"<documento>\n{neutralizar_delimitadores(texto)}\n</documento>",
    }


def _base64(conteudo: bytes) -> str:
    # b64encode não insere quebras de linha, como a API exige.
    return base64.b64encode(conteudo).decode("ascii")


def _preparar_txt(conteudo: bytes, limites: LimitesIngestao):
    texto = conteudo.decode("utf-8-sig")
    if len(texto) > limites.max_caracteres_txt:
        return DocumentoRecusado(
            "text/plain",
            "DOCUMENTO_LONGO",
            f"O documento tem {len(texto)} caracteres, acima do limite de "
            f"{limites.max_caracteres_txt} para processamento automático. "
            "Envie apenas a lista de itens do pedido.",
        )
    if limites.mascarar_pii_txt:
        texto = mascarar_pii(texto)
    # A referência é o mesmo texto enviado: evidências do modelo vêm desse texto.
    return DocumentoPreparado("text/plain", [_bloco_documento(texto)], texto, True)


def _preparar_pdf(conteudo: bytes, limites: LimitesIngestao):
    try:
        leitor = PdfReader(BytesIO(conteudo))
        paginas = len(leitor.pages)
    except (PdfReadError, ValueError, KeyError, TypeError, RecursionError):
        return DocumentoRecusado(
            "application/pdf",
            "DOCUMENTO_ILEGIVEL",
            "Não foi possível abrir o PDF. Envie o arquivo novamente ou em outro formato.",
        )
    if paginas > limites.max_paginas:
        return DocumentoRecusado(
            "application/pdf",
            "DOCUMENTO_LONGO",
            f"O PDF tem {paginas} páginas, acima do limite de {limites.max_paginas} para "
            "processamento automático. Envie apenas as páginas com os itens do pedido.",
            paginas,
        )
    textos: list[str] = []
    for pagina in leitor.pages:
        try:
            textos.append(pagina.extract_text() or "")
        except Exception:  # Extração é auxiliar: o PDF segue para o modelo mesmo assim.
            textos.append("")
    sem_texto = [
        numero
        for numero, texto in enumerate(textos, 1)
        if caracteres_uteis(texto) < MIN_CARACTERES_UTEIS_PAGINA
    ]
    referencia = "\n".join(textos).strip() or None
    bloco = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": _base64(conteudo)},
    }
    # Verificável só se todas as páginas têm texto: uma página escaneada pode conter itens
    # cuja evidência não estaria na referência.
    return DocumentoPreparado(
        "application/pdf", [bloco], referencia, not sem_texto, paginas, sem_texto
    )


def _preparar_imagem(conteudo: bytes, content_type: str):
    if len(conteudo) > MAX_BYTES_IMAGEM:
        return DocumentoRecusado(
            content_type,
            "IMAGEM_GRANDE",
            "A imagem excede 5 MB. Envie uma foto com resolução menor ou um PDF.",
        )
    bloco = {
        "type": "image",
        "source": {"type": "base64", "media_type": content_type, "data": _base64(conteudo)},
    }
    return DocumentoPreparado(content_type, [bloco], None, False, 1, [1])


def preparar_documento(
    conteudo: bytes, content_type: str, limites: LimitesIngestao
) -> DocumentoPreparado | DocumentoRecusado:
    """Converte o original em blocos para a API, ou recusa sem chamar o modelo."""
    if content_type == "text/plain":
        return _preparar_txt(conteudo, limites)
    if content_type == "application/pdf":
        return _preparar_pdf(conteudo, limites)
    if content_type in {"image/png", "image/jpeg"}:
        return _preparar_imagem(conteudo, content_type)
    return DocumentoRecusado(
        content_type,
        "FORMATO_NAO_SUPORTADO",
        "Formato não suportado pelo processamento automático. Envie PDF, TXT, PNG ou JPEG.",
    )
