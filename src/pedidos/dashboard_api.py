from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Security, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from pedidos.config import get_settings
from pedidos.db import session_factory
from pedidos.document_service import DocumentService
from pedidos.schemas import (
    ClienteDetalhe,
    DocumentoView,
    ListagemInput,
    Page,
    PedidoResumo,
    ProdutoView,
)
from pedidos.service import CatalogService

router = APIRouter(prefix="/api", dependencies=[Security(HTTPBearer())])


def database_session():
    with session_factory()() as session:
        yield session


DB = Annotated[Session, Depends(database_session)]
Pagination = Annotated[ListagemInput, Query()]


@router.get("/produtos", response_model=Page[ProdutoView], tags=["Catálogo"])
def produtos(entrada: Pagination, session: DB):
    return CatalogService(session, get_settings()).listar_produtos(entrada)


@router.get("/clientes", response_model=Page[ClienteDetalhe], tags=["Catálogo"])
def clientes(entrada: Pagination, session: DB):
    return CatalogService(session, get_settings()).listar_clientes(entrada)


@router.get("/pedidos", response_model=Page[PedidoResumo], tags=["Pedidos"])
def pedidos(
    session: DB,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    documento_id: UUID | None = None,
):
    return CatalogService(session, get_settings()).listar_pedidos(
        ListagemInput(limit=limit, offset=offset), documento_id
    )


@router.post("/documentos", response_model=DocumentoView, status_code=201, tags=["Documentos"])
def anexar_documento(session: DB, arquivo: UploadFile = File(...)):
    settings = get_settings()
    try:
        content = arquivo.file.read(settings.max_upload_bytes + 1)
        return DocumentService(session, settings).criar(arquivo.filename or "", content)
    finally:
        arquivo.file.close()


@router.get("/documentos", response_model=Page[DocumentoView], tags=["Documentos"])
def documentos(
    session: DB, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000)
):
    return DocumentService(session, get_settings()).listar(
        ListagemInput(limit=limit, offset=offset)
    )


@router.get("/documentos/{documento_id}", response_model=DocumentoView, tags=["Documentos"])
def documento(documento_id: UUID, session: DB):
    return DocumentService(session, get_settings()).obter(documento_id)


@router.get("/documentos/{documento_id}/arquivo", tags=["Documentos"])
def baixar_documento(documento_id: UUID, session: DB):
    service = DocumentService(session, get_settings())
    metadata = service.obter(documento_id)
    return FileResponse(
        service.caminho_arquivo(documento_id),
        media_type=metadata.content_type,
        filename=metadata.nome_original,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )
