import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from pedidos.errors import DomainError

MAX_PDF_PAGES = 100
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


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
    if extension in IMAGE_EXTENSIONS:
        # Só a assinatura e a estrutura mínima: o backend não decodifica imagens.
        media_type = IMAGE_EXTENSIONS[extension]
        valid = (
            content.startswith(PNG_SIGNATURE) and content[12:16] == b"IHDR"
            if media_type == "image/png"
            else content.startswith(b"\xff\xd8\xff")
            and content.rstrip(b"\x00").endswith(b"\xff\xd9")
        )
        if not valid:
            raise DomainError(
                "ARQUIVO_INVALIDO", "A imagem está danificada ou não corresponde à extensão."
            )
        # Extensão de armazenamento canônica: storage_key comporta UUID + 4 caracteres.
        return ValidatedUpload(name, media_type, ".jpg" if media_type == "image/jpeg" else ".png")
    if extension != ".pdf" or not content.startswith(b"%PDF-"):
        raise DomainError("ARQUIVO_INVALIDO", "Envie um arquivo PDF, TXT UTF-8, PNG ou JPEG.")
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
