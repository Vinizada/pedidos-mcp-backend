"""Contrato de análises, mascaramento de PII e exposição MCP, sem PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pedidos.config import Settings
from pedidos.mcp_server import create_mcp
from pedidos.privacidade import mascarar_pii, mascarar_pii_json
from pedidos.schemas import AnaliseInput


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("CPF 123.456.789-09 do comprador", "CPF [CPF] do comprador"),
        ("cpf 12345678909", "cpf [CPF]"),
        ("CNPJ 12.345.678/0001-95", "CNPJ [CNPJ]"),
        ("cnpj 12345678000195", "cnpj [CNPJ]"),
        ("Fone (11) 98765-4321", "Fone [TELEFONE]"),
        ("whats +55 11 98765-4321", "whats [TELEFONE]"),
        ("tel 51 3333-4444", "tel [TELEFONE]"),
        ("contato: compras@mercado.com.br", "contato: [EMAIL]"),
    ],
)
def test_pii_is_masked(texto, esperado):
    assert mascarar_pii(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "EAN 2990000000712, 12 unidades",
        "MER-004, 4 pacotes",
        "Total R$ 1234.56 em 2026-11-07",
        "Cliente CLI-002",
        "café Aurora 500 g",
    ],
)
def test_business_identifiers_are_not_masked(texto):
    assert mascarar_pii(texto) == texto


def test_pii_masking_reaches_nested_json_strings_only():
    extracao = {
        "itens": [{"descricao": "ligar 11 98765-4321", "quantidade": 3, "ean": "2990000000033"}],
        "observacoes": ["e-mail a@b.com"],
        "referencia": None,
    }
    assert mascarar_pii_json(extracao) == {
        "itens": [{"descricao": "ligar [TELEFONE]", "quantidade": 3, "ean": "2990000000033"}],
        "observacoes": ["e-mail [EMAIL]"],
        "referencia": None,
    }


def analise(**overrides):
    payload = {
        "documento_id": str(uuid4()),
        "chave_idempotencia": "agente:doc-1:v1",
        "decisao": "pedir_esclarecimento",
        "perguntas": ["Qual marca?"],
        "modelo": "modelo-teste",
        "versao_prompt": "v1",
    }
    return payload | overrides


def test_analysis_contract_accepts_valid_payload():
    entrada = AnaliseInput(**analise())
    assert entrada.revisao_obrigatoria is False
    assert entrada.guardrails_acionados == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"decisao": "confirmar"},
        {"status": "confirmado"},
        {"chave_idempotencia": "com espaco"},
        {"tokens_entrada": -1},
        {"custo_estimado": "-0.01"},
        {"motivos_revisao": ["Texto Livre"]},
        {"motivos": [{"codigo": "minusculo", "mensagem": "x"}]},
        {"perguntas": ["x" * 501]},
        {
            "guardrails_acionados": [
                {"guardrail": "proveniencia", "decisao_original": "ok", "decisao_final": "falha"}
            ]
        },
    ],
)
def test_analysis_contract_rejects_invalid_fields(overrides):
    with pytest.raises(ValidationError):
        AnaliseInput(**analise(**overrides))


def test_analysis_extraction_size_is_bounded():
    with pytest.raises(ValidationError, match="64 KiB"):
        AnaliseInput(**analise(extracao={"itens": ["x" * 70_000]}))


def test_registrar_analise_is_exposed_as_idempotent_write_tool():
    server = create_mcp(Settings(api_token="mcp-contract-test-" + "x" * 32))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {
        "buscar_produtos",
        "consultar_disponibilidade",
        "buscar_cliente",
        "validar_carrinho",
        "criar_pedido_rascunho",
        "registrar_analise",
        "consultar_pedido",
    }
    annotations = tools["registrar_analise"].annotations
    assert annotations.readOnlyHint is False
    assert annotations.idempotentHint is True
    assert annotations.destructiveHint is False
