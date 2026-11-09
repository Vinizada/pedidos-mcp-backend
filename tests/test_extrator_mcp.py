"""Cliente MCP do agente: lista fechada de ferramentas, schemas strict, erros e documentos."""

import asyncio
import hashlib
from contextlib import nullcontext
from decimal import Decimal
from uuid import uuid4

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError, ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError
from test_integration import TEST_TOKEN
from test_integration import db as db
from test_integration import test_engine as test_engine

from extrator.config import AgenteSettings
from extrator.mcp_cliente import (
    FERRAMENTAS_LLM,
    RESTRICOES_SERVIDOR,
    ClienteMCP,
    DocumentoCorrompido,
    ErroFerramenta,
    ErroRecurso,
    FerramentaNaoPermitida,
    FerramentasIndisponiveis,
    schema_estrito,
)
from pedidos.config import Settings
from pedidos.mcp_server import create_mcp

ESCRITA = {"criar_pedido_rascunho", "registrar_analise"}


def run_with_client(server, scenario):
    # Como o orquestrador fará: a exceção tipada é capturada dentro da sessão MCP.
    async def main():
        async with create_connected_server_and_client_session(server) as session:
            try:
                return await scenario(ClienteMCP(session)), None
            except Exception as exc:
                return None, exc

    result, error = asyncio.run(main())
    if error is not None:
        raise error
    return result


def walk(node):
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key == "properties":
                for sub in value.values():
                    yield from walk(sub)
            elif key not in {"default", "enum", "const", "examples"}:
                yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def real_server(**settings):
    return create_mcp(
        Settings(api_token="agent-client-test-" + "x" * 32, search_mode="lexical", **settings)
    )


# --- Configuração -------------------------------------------------------------------------


def test_settings_defaults_and_empty_api_key_is_absent(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = AgenteSettings(_env_file=None)
    assert settings.anthropic_api_key is None
    assert settings.llm_model == "claude-sonnet-5-5"
    assert settings.llm_effort == "medium"
    assert settings.api_base_url == "http://backend:8000"
    assert settings.preco_modelo.entrada == Decimal("2.00")


def test_settings_require_price_for_selected_model(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    with pytest.raises(ValidationError, match="PRECOS_MODELOS"):
        AgenteSettings(_env_file=None, llm_model="modelo-sem-preco")


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_effort": "turbo"},
        {"agente_max_paginas": 0},
        {"agente_max_custo_doc_usd": "0"},
        {"api_token": "curto"},
    ],
)
def test_settings_reject_invalid_limits(monkeypatch, overrides):
    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    with pytest.raises(ValidationError):
        AgenteSettings(_env_file=None, **overrides)


# --- Conversão de schemas -----------------------------------------------------------------


def test_strict_schema_inlines_refs_and_moves_unsupported_constraints():
    schema = {
        "type": "object",
        "title": "Args",
        "properties": {
            "entrada": {"$ref": "#/$defs/Busca"},
            "pattern": {"type": "string", "maxLength": 3},
        },
        "required": ["entrada"],
        "$defs": {
            "Busca": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "minLength": 2, "description": "Texto."},
                    "limite": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "additionalProperties": False,
            }
        },
    }
    result = schema_estrito(schema)
    assert "$defs" not in result and "title" not in result
    entrada = result["properties"]["entrada"]
    assert entrada["additionalProperties"] is False
    assert entrada["properties"]["consulta"] == {
        "type": "string",
        "description": "Texto. Validado no servidor: minLength=2.",
    }
    assert entrada["properties"]["limite"]["default"] == 5
    assert "minimum=1" in entrada["properties"]["limite"]["description"]
    assert entrada["properties"]["ids"]["minItems"] == 1
    # Campo chamado "pattern" é nome de propriedade, não palavra-chave do schema.
    assert "pattern" in result["properties"]
    assert result["additionalProperties"] is False


def test_strict_schema_rejects_recursion():
    schema = {
        "type": "object",
        "properties": {"no": {"$ref": "#/$defs/No"}},
        "$defs": {"No": {"type": "object", "properties": {"filho": {"$ref": "#/$defs/No"}}}},
    }
    with pytest.raises(ValueError, match="recursivo"):
        schema_estrito(schema)


def test_real_backend_tools_become_strict_read_only_definitions():
    tools = run_with_client(real_server(), lambda client: client.ferramentas_llm())
    assert [tool["name"] for tool in tools] == list(FERRAMENTAS_LLM)
    assert not ESCRITA & {tool["name"] for tool in tools}
    for tool in tools:
        assert tool["strict"] is True
        assert tool["description"]
        for node in walk(tool["input_schema"]):
            assert "$ref" not in node and "$defs" not in node
            assert not RESTRICOES_SERVIDOR & node.keys(), (tool["name"], node)
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False


# --- Lista fechada ------------------------------------------------------------------------


def fake_server(names, calls=None):
    server = FastMCP("falso")

    def make_handler(name):
        def handler(entrada: dict) -> dict:
            if calls is not None:
                calls.append(name)
            return {"ferramenta": name}

        return handler

    for name in names:
        server.add_tool(make_handler(name), name=name, description=f"Ferramenta {name}.")
    return server


def test_write_tools_never_reach_the_model_even_if_listed():
    names = [*reversed(FERRAMENTAS_LLM), *sorted(ESCRITA), "consultar_pedido", "extra"]
    tools = run_with_client(fake_server(names), lambda client: client.ferramentas_llm())
    assert [tool["name"] for tool in tools] == list(FERRAMENTAS_LLM)


def test_missing_read_tool_fails_instead_of_degrading():
    server = fake_server(FERRAMENTAS_LLM[:3])
    with pytest.raises(FerramentasIndisponiveis, match="validar_carrinho"):
        run_with_client(server, lambda client: client.ferramentas_llm())


@pytest.mark.parametrize("name", [*sorted(ESCRITA), "consultar_pedido", "inexistente"])
def test_model_requested_call_outside_allowlist_is_refused_before_sending(name):
    calls = []
    server = fake_server([*FERRAMENTAS_LLM, *ESCRITA, "consultar_pedido"], calls)
    with pytest.raises(FerramentaNaoPermitida):
        run_with_client(server, lambda client: client.chamar_para_llm(name, {"entrada": {}}))
    assert calls == []


def test_allowed_model_call_and_orchestrator_call_reach_backend():
    calls = []
    server = fake_server([*FERRAMENTAS_LLM, *ESCRITA], calls)

    async def scenario(client):
        first = await client.chamar_para_llm("buscar_produtos", {"entrada": {}})
        second = await client.chamar("registrar_analise", {"entrada": {}})
        return first, second

    first, second = run_with_client(server, scenario)
    assert first == {"ferramenta": "buscar_produtos"}
    assert second == {"ferramenta": "registrar_analise"}
    assert calls == ["buscar_produtos", "registrar_analise"]


# --- Erros --------------------------------------------------------------------------------


def test_backend_business_error_is_typed():
    server = FastMCP("falso")

    @server.tool(name="criar_pedido_rascunho")
    def criar(entrada: dict) -> dict:
        raise ToolError(
            '{"codigo":"CARRINHO_INVALIDO","mensagem":"Corrija o carrinho.",'
            '"problemas":[{"codigo":"SEM_PRECO","mensagem":"x"}]}'
        )

    with pytest.raises(ErroFerramenta) as raised:
        run_with_client(server, lambda c: c.chamar("criar_pedido_rascunho", {"entrada": {}}))
    assert raised.value.codigo == "CARRINHO_INVALIDO"
    assert raised.value.problemas[0]["codigo"] == "SEM_PRECO"
    assert raised.value.como_dict()["mensagem"] == "Corrija o carrinho."


def test_argument_validation_error_is_typed():
    async def scenario(client):
        return await client.chamar_para_llm("buscar_produtos", {"entrada": {"consulta": "x"}})

    with pytest.raises(ErroFerramenta) as raised:
        run_with_client(real_server(), scenario)
    assert raised.value.codigo == "ARGUMENTOS_INVALIDOS"
    assert not raised.value.mensagem.startswith("Error executing tool")
    assert raised.value.ferramenta == "buscar_produtos"


# --- Documentos ---------------------------------------------------------------------------


def document_server(content: bytes, sha256: str, fail_file=False):
    server = FastMCP("falso")
    document_id = uuid4()

    @server.resource("pedidos://documentos/{documento_id}", mime_type="application/json")
    def metadata(documento_id: str) -> str:
        return (
            f'{{"documento_id":"{document_id}","nome_original":"p.txt",'
            f'"content_type":"text/plain","sha256":"{sha256}"}}'
        )

    @server.resource(
        "pedidos://documentos/{documento_id}/arquivo", mime_type="application/octet-stream"
    )
    def arquivo(documento_id: str) -> bytes:
        if fail_file:
            raise ResourceError("Arquivo indisponível no armazenamento.")
        return content

    return server, document_id


def test_document_download_checks_sha256():
    content = "2 pacotes de café".encode()
    server, document_id = document_server(content, hashlib.sha256(content).hexdigest())
    document = run_with_client(server, lambda client: client.baixar_documento(document_id))
    assert document.conteudo == content
    assert document.content_type == "text/plain"


def test_document_with_divergent_hash_is_refused():
    server, document_id = document_server(b"conteudo trocado", "0" * 64)
    with pytest.raises(DocumentoCorrompido):
        run_with_client(server, lambda client: client.baixar_documento(document_id))


def test_unavailable_resource_is_typed():
    server, document_id = document_server(b"x", "0" * 64, fail_file=True)
    with pytest.raises(ErroRecurso):
        run_with_client(server, lambda client: client.baixar_documento(document_id))


# --- Integração com o backend real --------------------------------------------------------


@pytest.mark.integration
def test_client_against_real_backend_reads_document_and_calls_tools(db, tmp_path, monkeypatch):
    from pedidos import mcp_server
    from pedidos.document_service import DocumentService

    monkeypatch.setattr(mcp_server, "session_factory", lambda: lambda: nullcontext(db))
    settings = Settings(api_token=TEST_TOKEN, search_mode="lexical", upload_dir=str(tmp_path))
    content = b"Cliente CLI-001: MER-001, 2 unidades."
    uploaded = DocumentService(db, settings).criar("pedido.txt", content)

    async def scenario(client):
        document = await client.baixar_documento(uploaded.documento_id)
        search = await client.chamar_para_llm(
            "buscar_produtos", {"entrada": {"consulta": "MER-001"}}
        )
        customer = await client.chamar_para_llm(
            "buscar_cliente", {"entrada": {"consulta": "CLI-001"}}
        )
        return document, search, customer

    document, search, customer = run_with_client(create_mcp(settings), scenario)
    assert document.conteudo == content
    assert document.sha256 == uploaded.sha256
    assert document.metadados["status"] == "aguardando_agente"
    assert [c["sku"] for c in search["candidatos"]] == ["MER-001"]
    assert customer["clientes"][0]["codigo"] == "CLI-001"


@pytest.mark.integration
def test_unknown_document_is_a_resource_error(db, tmp_path, monkeypatch):
    from pedidos import mcp_server

    monkeypatch.setattr(mcp_server, "session_factory", lambda: lambda: nullcontext(db))
    server = real_server(upload_dir=str(tmp_path))
    with pytest.raises(ErroRecurso) as raised:
        run_with_client(server, lambda client: client.ler_metadados(uuid4()))
    assert raised.value.codigo == "DOCUMENTO_NAO_ENCONTRADO"
