"""Contrato HTTP e proteções do endpoint, sem PostgreSQL ou execução de LLM."""

import asyncio
import importlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pedidos.config import get_settings
from pedidos.schemas import BuscaOutput

TEST_TOKEN = "http-tests-only-" + "x" * 32


@pytest.fixture
def http_app(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("SEARCH_MODE", "lexical")
    monkeypatch.setenv("ALLOWED_HOSTS", '["testserver", "localhost", "127.0.0.1"]')
    monkeypatch.setenv("ALLOWED_ORIGINS", '["http://localhost:8000"]')
    get_settings.cache_clear()
    module = importlib.import_module("pedidos.app")
    # Evita depender de importação anterior realizada por outra suíte.
    module = importlib.reload(module)

    def forbidden_database_access():
        raise AssertionError("Teste HTTP isolado não deve conectar ao banco")

    monkeypatch.setattr(module, "session_factory", forbidden_database_access)
    yield module
    get_settings.cache_clear()


@pytest.fixture
def client(http_app):
    # Sem context manager: não inicia lifespan MCP nem cria pool de banco.
    return TestClient(http_app.app, raise_server_exceptions=False)


def auth_headers(**extra):
    return {"Authorization": f"Bearer {TEST_TOKEN}", **extra}


@pytest.mark.parametrize("path", ["/api/produtos/buscar", "/mcp"])
@pytest.mark.parametrize("authorization", [None, "Bearer incorreto", "Basic invalido"])
def test_private_endpoints_require_valid_bearer(client, path, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.post(path, json={"consulta": "café"}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TEST_TOKEN not in response.text


def test_valid_bearer_reaches_rest_handler(http_app, client, monkeypatch):
    seen = []

    def fake_execute(method, entrada):
        seen.append((method, entrada.consulta))
        return BuscaOutput(consulta=entrada.consulta, modo="lexical", candidatos=[])

    monkeypatch.setattr(http_app, "execute", fake_execute)
    response = client.post(
        "/api/produtos/buscar", json={"consulta": "café"}, headers=auth_headers()
    )
    assert response.status_code == 200
    assert seen == [("buscar_produtos", "café")]
    assert response.json()["candidatos"] == []


def test_untrusted_host_is_rejected_before_handler(client):
    response = client.get("/health/live", headers={"Host": "evil.example"})
    assert response.status_code == 400


@pytest.mark.parametrize("path", ["/health/live", "/api/produtos/buscar", "/mcp"])
def test_untrusted_origin_is_rejected(client, path):
    response = client.get(path, headers=auth_headers(Origin="https://evil.example"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Origin não permitido"


def test_allowed_origin_can_access_public_health(client):
    response = client.get("/health/live", headers={"Origin": "http://localhost:8000"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_oversized_request_is_rejected_before_json_parsing(client):
    response = client.post(
        "/api/produtos/buscar",
        content=b"x" * (262144 + 1),
        headers=auth_headers(**{"Content-Type": "application/json"}),
    )
    assert response.status_code == 413


@pytest.mark.parametrize("sizes, expected", [([131072, 131072], 200), ([131072, 131073], 413)])
def test_chunked_body_limit_cannot_be_bypassed(http_app, sizes, expected):
    async def run_request():
        messages = [
            {"type": "http.request", "body": b"x" * size, "more_body": i < len(sizes) - 1}
            for i, size in enumerate(sizes)
        ]
        sent = []
        downstream_received = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        async def downstream(scope, downstream_receive, downstream_send):
            downstream_received.append(await downstream_receive())
            await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
            await downstream_send({"type": "http.response.body", "body": b"ok"})

        scope = {
            "type": "http",
            "path": "/api/produtos/buscar",
            "method": "POST",
            "headers": [(b"authorization", f"Bearer {TEST_TOKEN}".encode())],
        }
        await http_app.AccessMiddleware(downstream, http_app.settings)(scope, receive, send)
        assert sent[0]["status"] == expected
        if expected == 200:
            assert len(downstream_received[0]["body"]) == sum(sizes)
            assert downstream_received[0]["more_body"] is False
        else:
            assert downstream_received == []

    asyncio.run(run_request())


def test_unknown_input_fields_are_rejected_before_business_logic(client):
    response = client.post(
        "/api/produtos/buscar",
        json={"consulta": "café", "sql": "SELECT 1"},
        headers=auth_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_prices_cannot_be_overridden_through_http(client):
    response = client.post(
        "/api/pedidos",
        json={
            "cliente_id": str(uuid4()),
            "itens": [{"produto_id": str(uuid4()), "quantidade": 1, "preco_unitario": "0.01"}],
            "chave_idempotencia": "http-pedido-001",
        },
        headers=auth_headers(),
    )
    assert response.status_code == 422
    assert any(error["type"] == "extra_forbidden" for error in response.json()["detail"])


def test_docs_are_public_and_openapi_declares_bearer(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert docs.headers["content-type"].startswith("text/html")
    schema_response = client.get("/openapi.json")
    assert schema_response.status_code == 200
    assert schema_response.headers["content-type"].startswith("application/json")
    schema = schema_response.json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/pedidos"]["post"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health/live"]["get"]
    assert TEST_TOKEN not in json.dumps(schema)


def test_database_error_is_not_exposed_by_readiness(client):
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
