import hmac

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from pedidos.config import Settings


class AccessMiddleware:
    """Autenticação compartilhada e limite de corpo; ASGI puro preserva transporte MCP."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin and origin not in self.settings.allowed_origins:
            return await JSONResponse({"detail": "Origin não permitido"}, 403)(scope, receive, send)
        public = {
            "/health/live",
            "/health/ready",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/favicon.ico",
        }
        public.update(
            {"/", "/interface", "/interface/", "/interface/app.js", "/interface/styles.css"}
        )
        if scope["path"] not in public:
            supplied = headers.get("authorization", "")
            expected = "Bearer " + self.settings.api_token.get_secret_value()
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                return await JSONResponse(
                    {"detail": "Bearer token necessário"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
        # Buffer limitado antes de entregar ao FastAPI/SDK (inclusive chunked).
        is_upload = scope["path"] == "/api/documentos" and scope["method"] == "POST"
        body_limit = self.settings.max_upload_bytes + 65536 if is_upload else 262144
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > body_limit:
                detail = (
                    "Arquivo excede o limite de upload" if is_upload else "Corpo excede 256 KiB"
                )
                return await JSONResponse({"detail": detail}, 413)(scope, receive, send)
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

        async def secured_send(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["Referrer-Policy"] = "same-origin"
                response_headers["Cache-Control"] = "no-store"
                if scope["path"].startswith("/interface"):
                    response_headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self'; style-src 'self'; "
                        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                        "base-uri 'none'; frame-ancestors 'none'"
                    )
            await send(message)

        await self.app(scope, replay, secured_send)
