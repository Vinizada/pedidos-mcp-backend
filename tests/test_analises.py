"""Análises do agente: idempotência, coerência, status derivado e revisão, em PostgreSQL."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from test_documents import authorization
from test_documents import document_api as document_api
from test_documents import documents as documents
from test_integration import db as db
from test_integration import order_input
from test_integration import service as service
from test_integration import test_engine as test_engine

from pedidos.analise_service import AnaliseService
from pedidos.models import AnaliseDocumento
from pedidos.schemas import AnaliseInput, ListagemInput
from pedidos.service import DomainError

pytestmark = pytest.mark.integration


@pytest.fixture
def analises(db):
    return AnaliseService(db)


def entrada(documento_id, **overrides):
    payload = {
        "documento_id": documento_id,
        "chave_idempotencia": f"agente:{uuid4()}:v1",
        "decisao": "pedir_esclarecimento",
        "perguntas": ["Qual das marcas compatíveis o cliente deseja?"],
        "modelo": "modelo-teste",
        "versao_prompt": "v1",
        "tokens_entrada": 1200,
        "tokens_saida": 150,
        "custo_estimado": "0.004200",
        "latencia_ms": 900,
    }
    return AnaliseInput(**(payload | overrides))


def criar_pedido(service, db, documento_id):
    return service.criar_pedido_rascunho(
        order_input(db).model_copy(update={"documento_id": documento_id})
    )


def analysis_count(db):
    return db.scalar(select(func.count()).select_from(AnaliseDocumento))


def test_clarification_is_registered_and_changes_document_status(documents, analises):
    documento = documents.criar("pedido.txt", b"cafe sem marca")
    registrada = analises.registrar_analise(entrada(documento.documento_id))
    assert registrada.decisao == "pedir_esclarecimento"
    assert registrada.repeticao_idempotente is False
    view = documents.obter(documento.documento_id)
    assert view.status == "aguardando_esclarecimento"
    assert [a.analise_id for a in view.analises] == [registrada.analise_id]
    assert view.analises[0].perguntas == ["Qual das marcas compatíveis o cliente deseja?"]


def test_same_key_and_content_returns_same_analysis(documents, analises, db):
    documento = documents.criar("pedido.txt", b"cafe sem marca")
    dados = entrada(documento.documento_id)
    first = analises.registrar_analise(dados)
    before = analysis_count(db)
    repeated = analises.registrar_analise(dados)
    assert repeated.analise_id == first.analise_id
    assert repeated.repeticao_idempotente is True
    assert analysis_count(db) == before


def test_same_key_with_different_content_is_conflict(documents, analises, db):
    documento = documents.criar("pedido.txt", b"cafe sem marca")
    dados = entrada(documento.documento_id)
    analises.registrar_analise(dados)
    before = analysis_count(db)
    with pytest.raises(DomainError) as raised:
        analises.registrar_analise(dados.model_copy(update={"perguntas": ["Outra pergunta?"]}))
    assert raised.value.codigo == "IDEMPOTENCIA_CONFLITO"
    assert analysis_count(db) == before


def test_draft_analysis_links_order_and_flags_review(documents, analises, service, db):
    documento = documents.criar("pedido.txt", b"2 unidades de cafe")
    pedido = criar_pedido(service, db, documento.documento_id)
    registrada = analises.registrar_analise(
        entrada(
            documento.documento_id,
            decisao="criar_rascunho",
            perguntas=[],
            pedido_id=pedido.pedido_id,
            revisao_obrigatoria=True,
            motivos_revisao=["casamento_por_descricao", "texto_nao_verificavel"],
            guardrails_acionados=[
                {
                    "guardrail": "atributos_conferidos",
                    "item_idx": 0,
                    "decisao_original": "criar_rascunho",
                    "decisao_final": "criar_rascunho",
                }
            ],
            texto_verificavel=False,
        )
    )
    assert registrada.pedido_id == pedido.pedido_id
    assert registrada.guardrails_acionados[0].guardrail == "atributos_conferidos"
    view = documents.obter(documento.documento_id)
    assert view.status == "pedido_criado"
    assert view.revisao_obrigatoria is True
    assert view.pedidos[0].revisao_obrigatoria is True
    consultado = service.consultar_pedido(pedido.pedido_id)
    assert consultado.revisao_obrigatoria is True
    assert consultado.motivos_revisao == ["casamento_por_descricao", "texto_nao_verificavel"]


def test_order_from_another_document_is_rejected(documents, analises, service, db):
    primeiro = documents.criar("primeiro.txt", b"2 unidades de cafe")
    segundo = documents.criar("segundo.txt", b"outro pedido")
    pedido = criar_pedido(service, db, primeiro.documento_id)
    before = analysis_count(db)
    with pytest.raises(DomainError) as raised:
        analises.registrar_analise(
            entrada(
                segundo.documento_id,
                decisao="criar_rascunho",
                perguntas=[],
                pedido_id=pedido.pedido_id,
            )
        )
    assert raised.value.codigo == "ANALISE_INCONSISTENTE"
    assert analysis_count(db) == before
    assert documents.obter(segundo.documento_id).status == "aguardando_agente"


@pytest.mark.parametrize(
    "overrides",
    [
        {"decisao": "criar_rascunho", "perguntas": []},
        {"decisao": "pedir_esclarecimento", "perguntas": []},
        {"decisao": "rejeitar", "perguntas": [], "motivos": []},
        {
            "decisao": "rejeitar",
            "perguntas": [],
            "motivos": [{"codigo": "NAO_GOSTEI", "mensagem": "Sem código do backend."}],
        },
        {"decisao": "falha", "pedido_id": str(uuid4())},
    ],
)
def test_incoherent_decisions_are_rejected(documents, analises, db, overrides):
    documento = documents.criar("pedido.txt", b"pedido")
    before = analysis_count(db)
    with pytest.raises(DomainError) as raised:
        analises.registrar_analise(entrada(documento.documento_id, **overrides))
    assert raised.value.codigo == "ANALISE_INCONSISTENTE"
    assert analysis_count(db) == before


def test_unknown_document_is_rejected(analises):
    with pytest.raises(DomainError) as raised:
        analises.registrar_analise(entrada(uuid4()))
    assert raised.value.codigo == "DOCUMENTO_NAO_ENCONTRADO"


def test_rejection_with_backend_code_marks_document_rejected(documents, analises):
    documento = documents.criar("pedido.txt", b"MER-100, 2 unidades")
    analises.registrar_analise(
        entrada(
            documento.documento_id,
            decisao="rejeitar",
            perguntas=[],
            motivos=[
                {"codigo": "PRODUTO_INATIVO", "mensagem": "MER-100 está inativo.", "item_idx": 0}
            ],
        )
    )
    view = documents.obter(documento.documento_id)
    assert view.status == "rejeitado"
    assert view.analises[0].motivos[0].codigo == "PRODUTO_INATIVO"


def test_latest_analysis_defines_status_and_failure_is_visible(documents, analises):
    documento = documents.criar("pedido.txt", b"pedido")
    analises.registrar_analise(entrada(documento.documento_id))
    analises.registrar_analise(
        entrada(
            documento.documento_id,
            decisao="falha",
            perguntas=[],
            motivos=[{"codigo": "ORCAMENTO_DOCUMENTO", "mensagem": "Teto de custo atingido."}],
        )
    )
    view = documents.obter(documento.documento_id)
    assert view.status == "falha_processamento"
    assert [a.decisao for a in view.analises] == ["falha", "pedir_esclarecimento"]


def test_pii_is_masked_before_storage(documents, analises, db):
    documento = documents.criar("pedido.txt", b"pedido")
    registrada = analises.registrar_analise(
        entrada(
            documento.documento_id,
            perguntas=["Confirmar com o comprador no (11) 98765-4321?"],
            motivos=[{"codigo": "CLIENTE_AMBIGUO", "mensagem": "CPF 123.456.789-09 informado."}],
            extracao={
                "itens": [{"descricao": "café", "ean": "2990000000033"}],
                "observacoes": ["enviar NF para compras@mercado.com.br"],
            },
        )
    )
    stored = db.get(AnaliseDocumento, registrada.analise_id)
    assert stored.perguntas == ["Confirmar com o comprador no [TELEFONE]?"]
    assert stored.motivos[0]["mensagem"] == "CPF [CPF] informado."
    assert stored.extracao["observacoes"] == ["enviar NF para [EMAIL]"]
    assert stored.extracao["itens"][0]["ean"] == "2990000000033"


def base_row(documento_id, **overrides):
    return {
        "id": uuid4(),
        "documento_id": documento_id,
        "decisao": "rejeitar",
        "modelo": "m",
        "versao_prompt": "v1",
        "chave_idempotencia": f"direta-{uuid4()}",
        "hash_requisicao": "0" * 64,
    } | overrides


@pytest.mark.parametrize(
    "overrides",
    [
        {"decisao": "criar_rascunho"},
        {"decisao": "pedir_esclarecimento"},
        {"decisao": "confirmado"},
        {"tokens_entrada": -1},
    ],
)
def test_database_enforces_analysis_coherence(documents, db, overrides):
    documento = documents.criar("pedido.txt", b"pedido")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            row = base_row(documento.documento_id, **overrides)
            db.execute(insert(AnaliseDocumento).values(**row))


def test_database_rejects_draft_decision_pointing_to_missing_order(documents, db):
    documento = documents.criar("pedido.txt", b"pedido")
    row = base_row(documento.documento_id, decisao="criar_rascunho", pedido_id=uuid4())
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(insert(AnaliseDocumento).values(**row))


def test_review_filters_documents_and_orders(documents, analises, service, db):
    marcado = documents.criar("marcado.txt", b"2 unidades de cafe")
    limpo = documents.criar("limpo.txt", b"2 unidades de cafe")
    pendente = documents.criar("pendente.txt", b"sem analise")
    pedido_marcado = criar_pedido(service, db, marcado.documento_id)
    pedido_limpo = service.criar_pedido_rascunho(
        order_input(db, products=2).model_copy(update={"documento_id": limpo.documento_id})
    )
    for documento, pedido, revisao in [
        (marcado, pedido_marcado, True),
        (limpo, pedido_limpo, False),
    ]:
        analises.registrar_analise(
            entrada(
                documento.documento_id,
                decisao="criar_rascunho",
                perguntas=[],
                pedido_id=pedido.pedido_id,
                revisao_obrigatoria=revisao,
                motivos_revisao=["possivel_injecao"] if revisao else [],
            )
        )
    page = ListagemInput(limit=100)
    com_revisao = {d.documento_id for d in documents.listar(page, revisao=True).items}
    sem_revisao = {d.documento_id for d in documents.listar(page, revisao=False).items}
    assert com_revisao == {marcado.documento_id}
    assert {limpo.documento_id, pendente.documento_id} <= sem_revisao
    assert marcado.documento_id not in sem_revisao
    pedidos = service.listar_pedidos(page, revisao=True)
    assert [p.pedido_id for p in pedidos.items] == [pedido_marcado.pedido_id]
    assert pedidos.items[0].motivos_revisao == ["possivel_injecao"]
    todos = {p.pedido_id for p in service.listar_pedidos(page).items}
    assert {pedido_marcado.pedido_id, pedido_limpo.pedido_id} <= todos


def test_analyses_api_lists_history_and_requires_token(document_api, db):
    client, _ = document_api
    uploaded = client.post(
        "/api/documentos",
        files={"arquivo": ("pedido.txt", b"cafe sem marca", "text/plain")},
        headers=authorization(),
    ).json()
    AnaliseService(db).registrar_analise(entrada(uploaded["documento_id"]))
    path = f"/api/documentos/{uploaded['documento_id']}/analises"
    assert client.get(path).status_code == 401
    listed = client.get(path, headers=authorization())
    assert listed.status_code == 200
    assert [a["decisao"] for a in listed.json()] == ["pedir_esclarecimento"]
    detail = client.get(f"/api/documentos/{uploaded['documento_id']}", headers=authorization())
    assert detail.json()["status"] == "aguardando_esclarecimento"
    filtered = client.get("/api/documentos?revisao=true", headers=authorization())
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0
    missing = client.get(f"/api/documentos/{uuid4()}/analises", headers=authorization())
    assert missing.status_code == 404


def test_daily_cost_sums_todays_analyses(documents, analises):
    from datetime import UTC, datetime, timedelta

    antes = analises.custo_do_dia()
    documento = documents.criar("pedido.txt", b"pedido")
    analises.registrar_analise(entrada(documento.documento_id, custo_estimado="0.012345"))
    analises.registrar_analise(entrada(documento.documento_id, custo_estimado="0.000655"))
    hoje = analises.custo_do_dia()
    assert hoje.custo_estimado - antes.custo_estimado == Decimal("0.013000")
    assert hoje.analises - antes.analises == 2
    amanha = analises.custo_do_dia(datetime.now(UTC).date() + timedelta(days=1))
    assert amanha.custo_estimado == 0 and amanha.analises == 0


def test_daily_cost_api_requires_token(document_api, db):
    client, _ = document_api
    assert client.get("/api/analises/custo").status_code == 401
    resposta = client.get("/api/analises/custo", headers=authorization())
    assert resposta.status_code == 200
    assert set(resposta.json()) == {"data", "custo_estimado", "analises"}
