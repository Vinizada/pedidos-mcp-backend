import hashlib
import logging
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from pedidos.analise_service import analise_resumo, revisoes_por_pedido
from pedidos.config import Settings
from pedidos.errors import DomainError
from pedidos.models import AnaliseDocumento, Documento, Pedido
from pedidos.schemas import DocumentoView, ListagemInput, Page, PedidoResumo
from pedidos.upload_validation import validate_upload

STATUS_POR_DECISAO = {
    "pedir_esclarecimento": "aguardando_esclarecimento",
    "rejeitar": "rejeitado",
    "falha": "falha_processamento",
}


def status_documento(pedidos: list[Pedido], analises: list[AnaliseDocumento]) -> str:
    """Pedido vinculado tem precedência; senão vale a análise mais recente."""
    if pedidos:
        return "pedido_criado"
    if analises:
        return STATUS_POR_DECISAO.get(analises[0].decisao, "aguardando_agente")
    return "aguardando_agente"


def pedido_resumo(pedido: Pedido, motivos_revisao: list[str] | None = None) -> PedidoResumo:
    return PedidoResumo(
        pedido_id=pedido.id,
        cliente_id=pedido.cliente_id,
        cliente_nome=pedido.cliente.nome,
        cliente_codigo=pedido.cliente.codigo,
        documento_id=pedido.documento_id,
        status=pedido.status,
        total=pedido.total,
        criado_em=pedido.criado_em,
        revisao_obrigatoria=bool(motivos_revisao),
        motivos_revisao=motivos_revisao or [],
    )


class DocumentService:
    """Persiste documentos originais e consulta os pedidos associados."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def _view(
        self,
        documento: Documento,
        pedidos: list[Pedido],
        analises: list[AnaliseDocumento] | None = None,
        revisoes: dict[UUID, list[str]] | None = None,
    ) -> DocumentoView:
        analises = analises or []
        revisoes = revisoes or {}
        return DocumentoView(
            documento_id=documento.id,
            nome_original=documento.nome_original,
            content_type=documento.content_type,
            tamanho_bytes=documento.tamanho_bytes,
            sha256=documento.sha256,
            criado_em=documento.criado_em,
            status=status_documento(pedidos, analises),
            pedidos=[pedido_resumo(p, revisoes.get(p.id)) for p in pedidos],
            analises=[analise_resumo(a) for a in analises],
            revisao_obrigatoria=bool(analises) and analises[0].revisao_obrigatoria,
        )

    def _analises(self, documento_ids: list[UUID]) -> dict[UUID, list[AnaliseDocumento]]:
        grouped: dict[UUID, list[AnaliseDocumento]] = {}
        if not documento_ids:
            return grouped
        for analise in self.session.scalars(
            select(AnaliseDocumento)
            .where(AnaliseDocumento.documento_id.in_(documento_ids))
            .order_by(AnaliseDocumento.criado_em.desc(), AnaliseDocumento.id.desc())
        ):
            grouped.setdefault(analise.documento_id, []).append(analise)
        return grouped

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
        return self._view(
            documento,
            pedidos,
            self._analises([documento_id]).get(documento_id, []),
            revisoes_por_pedido(self.session, [p.id for p in pedidos]),
        )

    def listar(self, entrada: ListagemInput, revisao: bool | None = None) -> Page[DocumentoView]:
        query = select(Documento)
        if revisao is not None:
            # Revisão é a da análise mais recente de cada documento.
            ordem = (
                func.row_number()
                .over(
                    partition_by=AnaliseDocumento.documento_id,
                    order_by=(AnaliseDocumento.criado_em.desc(), AnaliseDocumento.id.desc()),
                )
                .label("ordem")
            )
            ultima = select(
                AnaliseDocumento.documento_id, AnaliseDocumento.revisao_obrigatoria, ordem
            ).subquery()
            marcados = select(ultima.c.documento_id).where(
                ultima.c.ordem == 1, ultima.c.revisao_obrigatoria.is_(True)
            )
            query = query.where(
                Documento.id.in_(marcados) if revisao else Documento.id.not_in(marcados)
            )
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        documentos = list(
            self.session.scalars(
                query.order_by(Documento.criado_em.desc(), Documento.id.desc())
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
        analises = self._analises([d.id for d in documentos])
        revisoes = revisoes_por_pedido(self.session, [p.id for p in pedidos])
        return Page(
            items=[
                self._view(d, grouped.get(d.id, []), analises.get(d.id, []), revisoes)
                for d in documentos
            ],
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
