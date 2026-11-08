"""Resolução humana de esclarecimentos pela interface, contra PostgreSQL de teste."""

import asyncio
from uuid import uuid4

import pytest
from extrator_apoio import cliente, produto
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from test_documents import authorization
from test_documents import document_api as document_api
from test_documents import documents as documents
from test_integration import db as db
from test_integration import test_engine as test_engine

from pedidos.analise_service import AnaliseService
from pedidos.config import Settings
from pedidos.mcp_server import create_mcp
from pedidos.models import AnaliseDocumento, Pedido
from pedidos.schemas import AnaliseInput
from pedidos.service import DomainError

pytestmark = pytest.mark.integration
CAFE, SERRA = produto("MER-001"), produto("MER-002")
CLI1 = cliente("CLI-001")


def opcao(p):
    campos = ("produto_id", "sku", "ean", "nome", "marca", "embalagem", "preco_unitario")
    return {c: p[c] for c in campos} | {"estoque_disponivel": p["estoque_disponivel"]}


def pendencias(**cliente_extra):
    return {
        "cliente": {
            "situacao": "resolvido",
            "cliente_id": CLI1["cliente_id"],
            "codigo_documento": "CLI-001",
            "nome_documento": CLI1["nome"],
            "pergunta": None,
            "opcoes": [{k: CLI1[k] for k in ("cliente_id", "codigo", "nome")}],
        }
        | cliente_extra,
        "itens": [
            {
                "item_idx": 0,
                "descricao": "café moído de meio quilo — ligar (11) 98765-4321",
                "quantidade_texto": "10",
                "unidade_texto": "pacotes",
                "situacao": "pendente",
                "codigo": "AMBIGUIDADE",
                "pergunta": "Qual café: Aurora ou Serra?",
                "produto_id": None,
                "quantidade": 10,
                "opcoes": [opcao(CAFE), opcao(SERRA)],
            }
        ],
    }


def esclarecimento(analises, documento_id, **extra):
    dados = {
        "documento_id": documento_id,
        "chave_idempotencia": f"agente:{uuid4()}:v1",
        "decisao": "pedir_esclarecimento",
        "perguntas": ["Qual café: Aurora ou Serra?"],
        "modelo": "claude-haiku-5-5",
        "versao_prompt": "v1",
        "pendencias": pendencias(),
    }
    return analises.registrar_analise(AnaliseInput(**(dados | extra)))


@pytest.fixture
def pendente(document_api, db):
    client, _ = document_api
    enviado = client.post(
        "/api/documentos",
        files={
            "arquivo": (
                "pedido.txt",
                b"Quero 10 pacotes de cafe moido de meio quilo.",
                "text/plain",
            )
        },
        headers=authorization(),
    ).json()
    analise = esclarecimento(AnaliseService(db), enviado["documento_id"])
    return client, enviado["documento_id"], analise


def resolver(client, documento_id, analise_id, itens=None, **extra):
    corpo = {
        "analise_id": str(analise_id),
        "cliente_id": CLI1["cliente_id"],
        "itens": itens or [{"item_idx": 0, "produto_id": CAFE["produto_id"], "quantidade": 10}],
    } | extra
    return client.post(
        f"/api/documentos/{documento_id}/resolucao", json=corpo, headers=authorization()
    )


def contar(db, modelo, documento_id):
    return db.scalar(
        select(func.count()).select_from(modelo).where(modelo.documento_id == documento_id)
    )


def test_human_resolution_creates_linked_draft_and_closes_clarification(pendente, db):
    client, documento_id, analise = pendente
    detalhe = client.get(f"/api/documentos/{documento_id}", headers=authorization()).json()
    assert detalhe["status"] == "aguardando_esclarecimento"
    assert detalhe["analises"][0]["pendencias"]["itens"][0]["codigo"] == "AMBIGUIDADE"

    resposta = resolver(
        client, documento_id, analise.analise_id, observacao="Cliente escolheu Aurora."
    )
    assert resposta.status_code == 201, resposta.text
    pedido = resposta.json()
    assert pedido["documento_id"] == documento_id
    assert [(i["ean"], i["quantidade"]) for i in pedido["itens"]] == [(CAFE["ean"], 10)]

    detalhe = client.get(f"/api/documentos/{documento_id}", headers=authorization()).json()
    assert detalhe["status"] == "pedido_criado"
    ultima = detalhe["analises"][0]
    assert ultima["modelo"] == "humano"
    assert ultima["decisao"] == "criar_rascunho"
    assert ultima["motivos"][0] == {
        "codigo": "RESOLUCAO_HUMANA",
        "mensagem": "Cliente escolheu Aurora.",
        "item_idx": None,
    }


def test_retry_is_idempotent_and_changed_payload_conflicts(pendente, db):
    client, documento_id, analise = pendente
    primeiro = resolver(client, documento_id, analise.analise_id)
    repetido = resolver(client, documento_id, analise.analise_id)
    assert repetido.status_code == 201
    assert repetido.json()["pedido_id"] == primeiro.json()["pedido_id"]
    assert contar(db, Pedido, documento_id) == 1
    assert contar(db, AnaliseDocumento, documento_id) == 2

    outro = resolver(
        client,
        documento_id,
        analise.analise_id,
        itens=[{"item_idx": 0, "produto_id": SERRA["produto_id"], "quantidade": 10}],
    )
    assert outro.status_code == 409
    assert outro.json()["codigo"] == "IDEMPOTENCIA_CONFLITO"


def test_repeated_products_are_consolidated(pendente):
    client, documento_id, analise = pendente
    itens = [
        {"item_idx": 0, "produto_id": CAFE["produto_id"], "quantidade": 4},
        {"produto_id": CAFE["produto_id"], "quantidade": 6},
    ]
    resposta = resolver(client, documento_id, analise.analise_id, itens=itens)
    assert resposta.status_code == 201
    assert [i["quantidade"] for i in resposta.json()["itens"]] == [10]


def test_stale_analysis_is_refused(pendente, db):
    client, documento_id, antiga = pendente
    esclarecimento(AnaliseService(db), documento_id)
    resposta = resolver(client, documento_id, antiga.analise_id)
    assert resposta.status_code == 409
    assert resposta.json()["codigo"] == "RESOLUCAO_INVALIDA"
    assert contar(db, Pedido, documento_id) == 0


def test_only_clarifications_can_be_resolved(document_api, db):
    client, _ = document_api
    enviado = client.post(
        "/api/documentos",
        files={"arquivo": ("pedido.txt", b"MER-100, 2 unidades", "text/plain")},
        headers=authorization(),
    ).json()
    rejeicao = AnaliseService(db).registrar_analise(
        AnaliseInput(
            documento_id=enviado["documento_id"],
            chave_idempotencia=f"agente:{uuid4()}:v1",
            decisao="rejeitar",
            motivos=[{"codigo": "PRODUTO_INATIVO", "mensagem": "Inativo."}],
            modelo="m",
            versao_prompt="v1",
        )
    )
    resposta = resolver(client, enviado["documento_id"], rejeicao.analise_id)
    assert resposta.status_code == 409
    assert resposta.json()["codigo"] == "RESOLUCAO_INVALIDA"


def test_backend_validation_still_applies(pendente, db):
    client, documento_id, analise = pendente
    itens = [{"item_idx": 0, "produto_id": CAFE["produto_id"], "quantidade": 100_000}]
    resposta = resolver(client, documento_id, analise.analise_id, itens=itens)
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert corpo["codigo"] == "CARRINHO_INVALIDO"
    assert corpo["problemas"][0]["codigo"] == "ESTOQUE_INSUFICIENTE"
    assert contar(db, Pedido, documento_id) == 0
    detalhe = client.get(f"/api/documentos/{documento_id}", headers=authorization()).json()
    assert detalhe["status"] == "aguardando_esclarecimento"


@pytest.mark.parametrize(
    "corpo",
    [
        {"itens": []},
        {"itens": [{"produto_id": str(uuid4()), "quantidade": 0}]},
        {"itens": [{"produto_id": str(uuid4()), "quantidade": 1, "preco": "0.01"}]},
        {"cliente_id": "não-é-uuid"},
    ],
)
def test_invalid_resolution_payloads_are_rejected(pendente, corpo):
    client, documento_id, analise = pendente
    base = {
        "analise_id": str(analise.analise_id),
        "cliente_id": CLI1["cliente_id"],
        "itens": [{"produto_id": CAFE["produto_id"], "quantidade": 1}],
    }
    resposta = client.post(
        f"/api/documentos/{documento_id}/resolucao", json=base | corpo, headers=authorization()
    )
    assert resposta.status_code == 422


def test_resolution_requires_token_and_existing_document(pendente):
    client, documento_id, analise = pendente
    sem_token = client.post(f"/api/documentos/{documento_id}/resolucao", json={})
    assert sem_token.status_code == 401
    desconhecido = resolver(client, uuid4(), analise.analise_id)
    assert desconhecido.status_code == 404


def test_pending_texts_are_masked_and_only_allowed_on_clarification(documents, db):
    analises = AnaliseService(db)
    doc = documents.criar("pedido.txt", b"pedido")
    registrada = esclarecimento(analises, doc.documento_id)
    descricao = registrada.pendencias.itens[0].descricao
    assert "98765-4321" not in descricao and "[TELEFONE]" in descricao
    assert str(registrada.pendencias.itens[0].opcoes[0].produto_id) == CAFE["produto_id"]
    with pytest.raises(DomainError) as raised:
        analises.registrar_analise(
            AnaliseInput(
                documento_id=doc.documento_id,
                chave_idempotencia=f"agente:{uuid4()}:v1",
                decisao="falha",
                motivos=[{"codigo": "TIMEOUT", "mensagem": "x"}],
                modelo="m",
                versao_prompt="v1",
                pendencias=pendencias(),
            )
        )
    assert raised.value.codigo == "ANALISE_INCONSISTENTE"
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                insert(AnaliseDocumento).values(
                    id=uuid4(),
                    documento_id=doc.documento_id,
                    decisao="rejeitar",
                    modelo="m",
                    versao_prompt="v1",
                    chave_idempotencia=f"direta-{uuid4()}",
                    hash_requisicao="0" * 64,
                    pendencias={"cliente": {}, "itens": []},
                )
            )


def test_resolution_is_not_exposed_to_agents_over_mcp():
    server = create_mcp(Settings(api_token="mcp-contract-test-" + "x" * 32))
    nomes = {tool.name for tool in asyncio.run(server.list_tools())}
    assert not any("resol" in nome for nome in nomes)
