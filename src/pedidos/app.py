import logging
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryFile
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, FastAPI, Security
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBearer
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from pedidos.config import get_settings
from pedidos.dashboard_api import router as dashboard_router
from pedidos.db import get_engine, session_factory
from pedidos.http_security import AccessMiddleware
from pedidos.mcp_server import create_mcp
from pedidos.schemas import (
    BuscaInput,
    BuscaOutput,
    CarrinhoInput,
    CarrinhoOutput,
    ClientesOutput,
    DisponibilidadeOutput,
    PedidoInput,
    PedidoView,
    ProdutoIdsInput,
)
from pedidos.service import CatalogService, DomainError

settings = get_settings()
mcp = create_mcp(settings)
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        get_engine().dispose()


app = FastAPI(title="Pedidos MCP Backend", version="0.1.0", lifespan=lifespan)


app.add_middleware(AccessMiddleware, settings=settings)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


@app.exception_handler(DomainError)
async def business_error(request, exc: DomainError):
    status = {
        "PEDIDO_NAO_ENCONTRADO": 404,
        "IDEMPOTENCIA_CONFLITO": 409,
        "RESOLUCAO_INVALIDA": 409,
        "INDICE_INDISPONIVEL": 503,
        "DOCUMENTO_NAO_ENCONTRADO": 404,
        "ARQUIVO_INDISPONIVEL": 404,
        "ARQUIVO_MUITO_GRANDE": 413,
    }.get(exc.codigo, 422)
    return JSONResponse(exc.as_dict(), status_code=status)


@app.exception_handler(Exception)
async def internal_error(request, exc):
    logging.getLogger(__name__).error("Erro interno (%s)", type(exc).__name__)
    return JSONResponse({"codigo": "ERRO_INTERNO", "mensagem": "Falha interna do backend."}, 500)


@app.get("/health/live", tags=["health"])
def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready():
    try:
        with session_factory()() as session:
            expected = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
            actual = set(session.scalars(text("SELECT version_num FROM alembic_version")))
            if actual != expected:
                return JSONResponse({"status": "unavailable"}, 503)
            session.execute(text("SELECT documento_id FROM pedido_cabecalho LIMIT 1"))
        with TemporaryFile(dir=settings.upload_dir) as probe:
            probe.write(b"1")
            probe.flush()
        return {"status": "ready"}
    except Exception:
        return JSONResponse({"status": "unavailable"}, 503)


router = APIRouter(prefix="/api", dependencies=[Security(HTTPBearer())])


def execute(method, entrada):
    with session_factory()() as session:
        return getattr(CatalogService(session, settings), method)(entrada)


@router.post("/produtos/buscar", response_model=BuscaOutput)
def buscar_produtos(entrada: BuscaInput):
    return execute("buscar_produtos", entrada)


@router.post("/produtos/disponibilidade", response_model=DisponibilidadeOutput)
def disponibilidade(entrada: ProdutoIdsInput):
    return execute("consultar_disponibilidade", entrada)


@router.post("/clientes/buscar", response_model=ClientesOutput)
def buscar_cliente(entrada: BuscaInput):
    return execute("buscar_cliente", entrada)


@router.post("/carrinho/validar", response_model=CarrinhoOutput)
def validar_carrinho(entrada: CarrinhoInput):
    return execute("validar_carrinho", entrada)


@router.post("/pedidos", response_model=PedidoView)
def criar_pedido(entrada: PedidoInput):
    return execute("criar_pedido_rascunho", entrada)


@router.get("/pedidos/{pedido_id}", response_model=PedidoView)
def consultar_pedido(pedido_id: UUID):
    return execute("consultar_pedido", pedido_id)


app.include_router(router)
app.include_router(dashboard_router)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/interface")


@app.get("/interface", include_in_schema=False)
def interface():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/interface", StaticFiles(directory=STATIC_DIR, html=True, check_dir=False))
# Por último: a aplicação MCP trata /mcp; rotas REST acima mantêm precedência.
app.mount("/", mcp_app)
