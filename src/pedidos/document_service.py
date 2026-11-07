import hashlib
import logging
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from pedidos.config import Settings
from pedidos.errors import DomainError
from pedidos.models import Documento, Pedido
from pedidos.schemas import DocumentoView, ListagemInput, Page, PedidoResumo
from pedidos.upload_validation import validate_upload


def pedido_resumo(pedido: Pedido) -> PedidoResumo:
    return PedidoResumo(
        pedido_id=pedido.id,
        cliente_id=pedido.cliente_id,
        cliente_nome=pedido.cliente.nome,
        cliente_codigo=pedido.cliente.codigo,
        documento_id=pedido.documento_id,
        status=pedido.status,
        total=pedido.total,
        criado_em=pedido.criado_em,
    )


class DocumentService:
    """Persiste documentos originais e consulta os pedidos associados."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def _view(self, documento: Documento, pedidos: list[Pedido]) -> DocumentoView:
        return DocumentoView(
            documento_id=documento.id,
            nome_original=documento.nome_original,
            content_type=documento.content_type,
            tamanho_bytes=documento.tamanho_bytes,
            sha256=documento.sha256,
            criado_em=documento.criado_em,
            status="pedido_criado" if pedidos else "aguardando_agente",
            pedidos=[pedido_resumo(p) for p in pedidos],
        )

    def criar(self, nome_original: str, content: bytes) -> DocumentoView:
        validated = validate_upload(nome_original, content, self.settings.max_upload_bytes)
        # Persistimos o tipo validado pelo conteúdo, independente do MIME recebido.
        document_id = uuid4()
        folder = Path(self.settings.upload_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        storage_key = f"{document_id}{validated.extension}"
        destination = folder / storage_key
        documento = Documento(
            id=document_id,
            nome_original=validated.filename,
            content_type=validated.media_type,
            tamanho_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=storage_key,
        )
        commit_attempted = False
        file_created = False
        try:
            with destination.open("xb") as stream:
                file_created = True
                destination.chmod(0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self.session.add(documento)
            self.session.flush()
            result = self._view(documento, [])
            commit_attempted = True
            self.session.commit()
            return result
        except Exception:
            try:
                self.session.rollback()
            finally:
                if file_created and not commit_attempted:
                    destination.unlink(missing_ok=True)
                elif commit_attempted:
                    # COMMIT pode ter sido aceito antes de perdermos a resposta.
                    # Preservar o arquivo permite reconciliar sem perder o original.
                    logging.getLogger(__name__).warning(
                        "Upload %s preservado após falha durante commit; conferir registro.",
                        document_id,
                    )
            raise

    def _get(self, documento_id: UUID) -> Documento:
        documento = self.session.get(Documento, documento_id)
        if documento is None:
            raise DomainError("DOCUMENTO_NAO_ENCONTRADO", "Documento não encontrado.")
        return documento

    def obter(self, documento_id: UUID) -> DocumentoView:
        documento = self._get(documento_id)
        pedidos = list(
            self.session.scalars(
                select(Pedido)
                .options(joinedload(Pedido.cliente))
                .where(Pedido.documento_id == documento_id)
                .order_by(Pedido.criado_em.desc(), Pedido.id.desc())
            )
        )
        return self._view(documento, pedidos)

    def listar(self, entrada: ListagemInput) -> Page[DocumentoView]:
        total = self.session.scalar(select(func.count()).select_from(Documento))
        documentos = list(
            self.session.scalars(
                select(Documento)
                .order_by(Documento.criado_em.desc(), Documento.id.desc())
                .limit(entrada.limit)
                .offset(entrada.offset)
            )
        )
        # Consulta em lote, sem uma ida ao banco para cada documento listado.
        pedidos = list(
            self.session.scalars(
                select(Pedido)
                .options(joinedload(Pedido.cliente))
                .where(Pedido.documento_id.in_([d.id for d in documentos]))
                .order_by(Pedido.criado_em.desc(), Pedido.id.desc())
            )
        )
        grouped: dict[UUID, list[Pedido]] = {}
        for pedido in pedidos:
            grouped.setdefault(pedido.documento_id, []).append(pedido)
        return Page(
            items=[self._view(d, grouped.get(d.id, [])) for d in documentos],
            total=total,
            limit=entrada.limit,
            offset=entrada.offset,
        )

    def caminho_arquivo(self, documento_id: UUID) -> Path:
        documento = self._get(documento_id)
        folder = Path(self.settings.upload_dir).resolve()
        path = (folder / documento.storage_key).resolve()
        if path.parent != folder or not path.is_file():
            raise DomainError("ARQUIVO_INDISPONIVEL", "Arquivo indisponível no armazenamento.")
        return path
