import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from pedidos.errors import DomainError

MAX_PDF_PAGES = 100


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    media_type: str
    extension: str


def validate_upload(filename: str, content: bytes, max_bytes: int) -> ValidatedUpload:
    """Valida formato e limites sem extrair texto ou executar o conteúdo recebido."""
    if not content:
        raise DomainError("ARQUIVO_INVALIDO", "O arquivo está vazio.")
    if len(content) > max_bytes:
        raise DomainError(
            "ARQUIVO_MUITO_GRANDE", f"O arquivo excede o limite de {max_bytes} bytes."
        )
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if not name or len(name) > 200:
        raise DomainError("ARQUIVO_INVALIDO", "Use um nome de arquivo com até 200 caracteres.")
    extension = Path(name).suffix.lower()
    if extension == ".txt":
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DomainError("ARQUIVO_INVALIDO", "Salve o TXT com codificação UTF-8.") from None
        if "\x00" in decoded or not decoded.strip():
            raise DomainError("ARQUIVO_INVALIDO", "O TXT precisa conter texto legível.")
        return ValidatedUpload(name, "text/plain", extension)
    if extension != ".pdf" or not content.startswith(b"%PDF-"):
        raise DomainError("ARQUIVO_INVALIDO", "Envie um arquivo PDF ou TXT UTF-8.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DomainError("ARQUIVO_INVALIDO", "Envie um PDF sem senha ou criptografia.")
        pages = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError, RecursionError):
        raise DomainError(
            "ARQUIVO_INVALIDO", "O PDF está danificado ou possui estrutura inválida."
        ) from None
    if not 1 <= pages <= MAX_PDF_PAGES:
        raise DomainError("ARQUIVO_INVALIDO", f"O PDF deve ter entre 1 e {MAX_PDF_PAGES} páginas.")
    return ValidatedUpload(name, "application/pdf", extension)
