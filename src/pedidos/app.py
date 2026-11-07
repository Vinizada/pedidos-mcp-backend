import hmac
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, FastAPI, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from sqlalchemy import text
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pedidos.config import get_settings
from pedidos.db import get_engine, session_factory
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
    async with mcp.session_manager.run():
        yield
    get_engine().dispose()


app = FastAPI(title="Pedidos MCP Backend", version="0.1.0", lifespan=lifespan)


class AccessMiddleware:
    """Autenticação compartilhada e limite de corpo; ASGI puro preserva transporte MCP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return await JSONResponse({"detail": "Origin não permitido"}, 403)(scope, receive, send)
        public = {"/health/live", "/health/ready", "/docs", "/openapi.json", "/redoc"}
        if scope["path"] not in public:
            supplied = headers.get("authorization", "")
            expected = "Bearer " + settings.api_token.get_secret_value()
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                return await JSONResponse(
                    {"detail": "Bearer token necessário"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
        # Buffer limitado antes de entregar ao FastAPI/SDK (inclusive chunked).
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > 262144:
                return await JSONResponse({"detail": "Corpo excede 256 KiB"}, 413)(
                    scope, receive, send
                )
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


app.add_middleware(AccessMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


@app.exception_handler(DomainError)
async def business_error(request, exc: DomainError):
    status = {
        "PEDIDO_NAO_ENCONTRADO": 404,
        "IDEMPOTENCIA_CONFLITO": 409,
        "INDICE_INDISPONIVEL": 503,
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
            session.execute(text("SELECT 1 FROM produtos LIMIT 1"))
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
# Por último: a aplicação MCP trata /mcp; rotas REST acima mantêm precedência.
app.mount("/", mcp_app)
