from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Security, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from pedidos.analise_service import AnaliseService
from pedidos.config import get_settings
from pedidos.db import session_factory
from pedidos.document_service import DocumentService
from pedidos.resolucao_humana import ResolucaoService
from pedidos.schemas import (
    AnaliseView,
    ClienteDetalhe,
    CustoDiarioView,
    DocumentoView,
    ListagemInput,
    Page,
    PedidoResumo,
    PedidoView,
    ProdutoView,
    ResolucaoInput,
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
    revisao: bool | None = None,
):
    return CatalogService(session, get_settings()).listar_pedidos(
        ListagemInput(limit=limit, offset=offset), documento_id, revisao
    )


@router.post("/documentos", response_model=DocumentoView, status_code=201, tags=["Documentos"])
def anexar_documento(session: DB, arquivo: UploadFile = File(...)):
    settings = get_settings()
    try:
        content = arquivo.file.read(settings.max_upload_bytes + 1)
        return DocumentService(session, settings).criar(arquivo.filename or "", content)
    finally:
        arquivo.file.close()


@router.get("/analises/custo", response_model=CustoDiarioView, tags=["Documentos"])
def custo_analises(session: DB, data: date | None = None):
    return AnaliseService(session).custo_do_dia(data)


@router.get("/documentos", response_model=Page[DocumentoView], tags=["Documentos"])
def documentos(
    session: DB,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    revisao: bool | None = None,
):
    return DocumentService(session, get_settings()).listar(
        ListagemInput(limit=limit, offset=offset), revisao
    )


@router.get("/documentos/{documento_id}", response_model=DocumentoView, tags=["Documentos"])
def documento(documento_id: UUID, session: DB):
    return DocumentService(session, get_settings()).obter(documento_id)


@router.get(
    "/documentos/{documento_id}/analises",
    response_model=list[AnaliseView],
    tags=["Documentos"],
)
def analises_documento(documento_id: UUID, session: DB):
    return AnaliseService(session).listar_por_documento(documento_id)


@router.post(
    "/documentos/{documento_id}/resolucao",
    response_model=PedidoView,
    status_code=201,
    tags=["Documentos"],
)
def resolver_esclarecimento(documento_id: UUID, entrada: ResolucaoInput, session: DB):
    """Cria o rascunho a partir das escolhas de uma pessoa para um esclarecimento."""
    return ResolucaoService(session, get_settings()).resolver(documento_id, entrada)


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
