"""Uploads e vínculo documento/pedido, usando banco e diretório descartáveis."""

import hashlib
import importlib
import json
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import func, select
from test_integration import TEST_TOKEN, count_orders, order_input
from test_integration import db as db
from test_integration import service as service
from test_integration import test_engine as test_engine

from pedidos.config import Settings, get_settings
from pedidos.models import Pedido
from pedidos.service import DomainError

pytestmark = pytest.mark.integration


@pytest.fixture
def documents(db, tmp_path):
    from pedidos.document_service import DocumentService

    settings = Settings(api_token=TEST_TOKEN, search_mode="lexical", upload_dir=str(tmp_path))
    return DocumentService(db, settings)


def document_count(db):
    from pedidos.models import Documento

    return db.scalar(select(func.count()).select_from(Documento))


def pdf_bytes(*, pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("senha-apenas-para-teste")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_text_upload_preserves_bytes_and_registers_metadata(documents, tmp_path):
    content = "Pedido: café Aurora 500 g, 2 unidades.\n".encode()
    result = documents.criar("pedido.txt", content)
    assert result.nome_original == "pedido.txt"
    assert result.content_type == "text/plain"
    assert result.tamanho_bytes == len(content)
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert result.status == "aguardando_agente"
    assert result.pedidos == []
    path = documents.caminho_arquivo(result.documento_id)
    assert path.parent.resolve() == tmp_path.resolve()
    assert path.name == f"{result.documento_id}.txt"
    assert path.read_bytes() == content
    assert documents.obter(result.documento_id).documento_id == result.documento_id


def test_pdf_upload_preserves_original_bytes(documents):
    content = pdf_bytes()
    result = documents.criar("cotacao.pdf", content)
    assert result.content_type == "application/pdf"
    assert documents.caminho_arquivo(result.documento_id).read_bytes() == content
    assert result.status == "aguardando_agente"


@pytest.mark.parametrize("filename", ["../../../pedido.txt", r"..\..\pedido.txt"])
def test_client_filename_cannot_choose_storage_path(documents, tmp_path, filename):
    result = documents.criar(filename, b"2 unidades de cafe")
    assert result.nome_original == "pedido.txt"
    path = documents.caminho_arquivo(result.documento_id)
    assert path.parent.resolve() == tmp_path.resolve()
    assert path.name == f"{result.documento_id}.txt"


@pytest.mark.parametrize(
    "filename, content",
    [
        ("pedido.exe", b"arbitrary bytes"),
        ("pedido.pdf", b"not a pdf"),
        ("pedido.pdf", b"%PDF-1.4\n%truncated fake PDF\n"),
        ("pedido.txt", b"invalid utf8 \xff"),
        ("pedido.txt", b"contains\x00null"),
        ("pedido.txt", b""),
        ("pedido.pdf", b""),
    ],
)
def test_invalid_files_leave_neither_database_rows_nor_files(
    documents, db, tmp_path, filename, content
):
    before = document_count(db)
    with pytest.raises(DomainError) as raised:
        documents.criar(filename, content)
    assert raised.value.codigo == "ARQUIVO_INVALIDO"
    assert document_count(db) == before
    assert not list(tmp_path.iterdir())


def test_file_size_limit_is_enforced_before_storage(db, tmp_path):
    from pedidos.document_service import DocumentService

    settings = Settings(
        api_token=TEST_TOKEN, search_mode="lexical", upload_dir=str(tmp_path), max_upload_bytes=8
    )
    documents = DocumentService(db, settings)
    with pytest.raises(DomainError) as raised:
        documents.criar("pedido.txt", b"123456789")
    assert raised.value.codigo == "ARQUIVO_MUITO_GRANDE"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("method", ["obter", "caminho_arquivo"])
def test_unknown_document_is_a_domain_error(documents, method):
    with pytest.raises(DomainError) as raised:
        getattr(documents, method)(uuid4())
    assert raised.value.codigo == "DOCUMENTO_NAO_ENCONTRADO"


def test_commit_failure_preserves_file_when_database_outcome_may_be_unknown(
    documents, db, tmp_path, monkeypatch
):
    before = document_count(db)

    def fail_commit():
        raise RuntimeError("simulated database commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated database commit failure"):
        documents.criar("pedido.txt", b"Pedido de duas unidades")
    assert document_count(db) == before
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == b"Pedido de duas unidades"


def test_flush_failure_removes_file_before_commit_is_attempted(
    documents, db, tmp_path, monkeypatch
):
    before = document_count(db)

    def fail_flush():
        raise RuntimeError("simulated database flush failure")

    with monkeypatch.context() as patch:
        patch.setattr(db, "flush", fail_flush)
        with pytest.raises(RuntimeError, match="simulated database flush failure"):
            documents.criar("pedido.txt", b"Pedido de duas unidades")
    assert document_count(db) == before
    assert not list(tmp_path.iterdir())


def test_lost_commit_acknowledgement_preserves_committed_document_and_original(
    documents, db, tmp_path, monkeypatch
):
    from pedidos.models import Documento

    real_commit = db.commit
    content = b"Pedido recebido antes da perda de conexao"

    def commit_then_disconnect():
        real_commit()
        raise RuntimeError("commit succeeded but acknowledgement was lost")

    monkeypatch.setattr(db, "commit", commit_then_disconnect)
    with pytest.raises(RuntimeError, match="acknowledgement was lost"):
        documents.criar("ack.txt", content)
    persisted = db.scalar(select(Documento).where(Documento.nome_original == "ack.txt"))
    assert persisted is not None
    path = documents.caminho_arquivo(persisted.id)
    assert path.parent.resolve() == tmp_path.resolve()
    assert path.read_bytes() == content
    assert documents.obter(persisted.id).status == "aguardando_agente"


def test_destination_collision_does_not_delete_existing_file(documents, db, tmp_path, monkeypatch):
    from pedidos import document_service

    document_id = uuid4()
    destination = tmp_path / f"{document_id}.txt"
    original = b"Original previamente armazenado; nao deve ser apagado"
    destination.write_bytes(original)
    before = document_count(db)
    monkeypatch.setattr(document_service, "uuid4", lambda: document_id)

    with pytest.raises(FileExistsError):
        documents.criar("novo-pedido.txt", b"Novo upload em colisao")

    assert document_count(db) == before
    assert destination.read_bytes() == original
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize(
    "content", [pdf_bytes(pages=0), pdf_bytes(pages=101), pdf_bytes(encrypted=True)]
)
def test_unsupported_pdf_documents_are_rejected_without_storage(documents, db, tmp_path, content):
    before = document_count(db)
    with pytest.raises(DomainError) as raised:
        documents.criar("pedido.pdf", content)
    assert raised.value.codigo == "ARQUIVO_INVALIDO"
    assert document_count(db) == before
    assert not list(tmp_path.iterdir())


def test_document_listing_is_paginated(documents):
    from pedidos.schemas import ListagemInput

    ids = {
        documents.criar(f"pedido-{i}.txt", f"Pedido {i}".encode()).documento_id for i in range(3)
    }
    first = documents.listar(ListagemInput(limit=2, offset=0))
    second = documents.listar(ListagemInput(limit=2, offset=2))
    assert first.total == second.total == 3
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert {item.documento_id for item in [*first.items, *second.items]} == ids


def test_order_creation_links_document_and_changes_derived_status(documents, service, db):
    document = documents.criar("pedido.txt", b"2 unidades de cafe")
    order = order_input(db).model_copy(update={"documento_id": document.documento_id})
    created = service.criar_pedido_rascunho(order)
    assert created.documento_id == document.documento_id
    assert service.consultar_pedido(created.pedido_id).documento_id == document.documento_id
    updated = documents.obter(document.documento_id)
    assert updated.status == "pedido_criado"
    assert [item.pedido_id for item in updated.pedidos] == [created.pedido_id]
    assert updated.pedidos[0].total == created.total
    repeated = service.criar_pedido_rascunho(order)
    assert repeated.pedido_id == created.pedido_id
    assert repeated.repeticao_idempotente is True
    assert len(documents.obter(document.documento_id).pedidos) == 1


def test_unknown_document_does_not_create_an_order(service, db):
    order = order_input(db).model_copy(update={"documento_id": uuid4()})
    before = count_orders(db)
    with pytest.raises(DomainError) as raised:
        service.criar_pedido_rascunho(order)
    assert raised.value.codigo == "DOCUMENTO_NAO_ENCONTRADO"
    assert count_orders(db) == before


def test_changing_document_with_same_idempotency_key_is_conflict(documents, service, db):
    first = documents.criar("primeiro.txt", b"primeiro pedido")
    second = documents.criar("segundo.txt", b"segundo pedido")
    order = order_input(db).model_copy(update={"documento_id": first.documento_id})
    created = service.criar_pedido_rascunho(order)
    changed = order.model_copy(update={"documento_id": second.documento_id})
    with pytest.raises(DomainError) as raised:
        service.criar_pedido_rascunho(changed)
    assert raised.value.codigo == "IDEMPOTENCIA_CONFLITO"
    assert service.consultar_pedido(created.pedido_id).documento_id == first.documento_id
    assert documents.obter(second.documento_id).status == "aguardando_agente"


def test_legacy_order_without_document_keeps_idempotent_replay(service, db):
    order = order_input(db)
    created = service.criar_pedido_rascunho(order)
    legacy_payload = order.model_dump(mode="json", exclude={"chave_idempotencia", "documento_id"})
    legacy_payload["itens"] = sorted(legacy_payload["itens"], key=lambda item: item["produto_id"])
    legacy_hash = hashlib.sha256(json.dumps(legacy_payload, sort_keys=True).encode()).hexdigest()
    db.get(Pedido, created.pedido_id).hash_requisicao = legacy_hash
    db.commit()
    repeated = service.criar_pedido_rascunho(order)
    assert repeated.pedido_id == created.pedido_id
    assert repeated.repeticao_idempotente is True
    assert repeated.documento_id is None


@pytest.fixture
def document_api(db, tmp_path, monkeypatch):
    """Aplicação real com sessão transacional e diretório isolados por teste."""
    from pedidos import dashboard_api

    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("SEARCH_MODE", "lexical")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_HOSTS", '["testserver", "localhost", "127.0.0.1"]')
    get_settings.cache_clear()
    module = importlib.reload(importlib.import_module("pedidos.app"))
    settings = get_settings()
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: settings)

    def session_override():
        yield db

    module.app.dependency_overrides[dashboard_api.database_session] = session_override
    client = TestClient(module.app, raise_server_exceptions=False)
    yield client, settings
    client.close()
    module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def authorization():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/documentos"),
        ("get", "/api/documentos"),
        ("get", f"/api/documentos/{uuid4()}"),
        ("get", f"/api/documentos/{uuid4()}/arquivo"),
    ],
)
def test_document_api_requires_token(document_api, db, tmp_path, method, path):
    client, _ = document_api
    before = document_count(db)
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert document_count(db) == before
    assert not list(tmp_path.iterdir())


def test_document_api_upload_list_detail_and_attachment(document_api):
    client, _ = document_api
    content = "2 pacotes de café Aurora".encode()
    uploaded = client.post(
        "/api/documentos",
        files={"arquivo": ("pedido café.txt", content, "application/octet-stream")},
        headers=authorization(),
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    document_id = document["documento_id"]
    assert document["status"] == "aguardando_agente"
    assert document["content_type"] == "text/plain"
    assert document["sha256"] == hashlib.sha256(content).hexdigest()
    listed = client.get("/api/documentos?limit=1&offset=0", headers=authorization())
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["documento_id"] == document_id
    detail = client.get(f"/api/documentos/{document_id}", headers=authorization())
    assert detail.status_code == 200
    assert detail.json() == document
    downloaded = client.get(f"/api/documentos/{document_id}/arquivo", headers=authorization())
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"].startswith("text/plain")
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "pedido%20caf%C3%A9.txt" in downloaded.headers["content-disposition"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "no-store"


def test_document_api_serves_pdf_as_attachment_with_verified_type(document_api):
    client, _ = document_api
    content = pdf_bytes()
    uploaded = client.post(
        "/api/documentos",
        files={"arquivo": ("orcamento.pdf", content, "text/html")},
        headers=authorization(),
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["content_type"] == "application/pdf"
    downloaded = client.get(
        f"/api/documentos/{uploaded.json()['documento_id']}/arquivo", headers=authorization()
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="orcamento.pdf"'


@pytest.mark.parametrize(
    "filename, content, mime",
    [
        ("pedido.html", b"<script>alert(1)</script>", "text/html"),
        ("pedido.pdf", b"not a PDF", "application/pdf"),
        ("pedido.txt", b"", "text/plain"),
        ("pedido.txt", b"\xff\xfeinvalid utf8", "text/plain"),
    ],
)
def test_document_api_rejects_invalid_multipart_files(
    document_api, db, tmp_path, filename, content, mime
):
    client, _ = document_api
    before = document_count(db)
    response = client.post(
        "/api/documentos",
        files={"arquivo": (filename, content, mime)},
        headers=authorization(),
    )
    assert response.status_code == 422
    assert response.json()["codigo"] == "ARQUIVO_INVALIDO"
    assert document_count(db) == before
    assert not list(tmp_path.iterdir())


def test_document_api_rejects_missing_file(document_api):
    client, _ = document_api
    response = client.post("/api/documentos", data={"outro": "valor"}, headers=authorization())
    assert response.status_code == 422


def test_document_api_enforces_file_limit_and_total_multipart_limit(document_api, tmp_path):
    client, settings = document_api
    settings.max_upload_bytes = 8
    for content in [b"123456789", b"x" * (8 + 65536 + 1)]:
        response = client.post(
            "/api/documentos",
            files={"arquivo": ("pedido.txt", content, "text/plain")},
            headers=authorization(),
        )
        assert response.status_code == 413
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("suffix", ["", "/arquivo"])
def test_document_api_unknown_id_returns_404(document_api, suffix):
    client, _ = document_api
    response = client.get(f"/api/documentos/{uuid4()}{suffix}", headers=authorization())
    assert response.status_code == 404
    assert response.json()["codigo"] == "DOCUMENTO_NAO_ENCONTRADO"


def test_document_api_exposes_linked_order_and_filters_other_documents(document_api, service, db):
    client, _ = document_api
    ids = []
    for name in ["primeiro.txt", "segundo.txt"]:
        response = client.post(
            "/api/documentos",
            files={"arquivo": (name, b"2 unidades de cafe", "text/plain")},
            headers=authorization(),
        )
        assert response.status_code == 201
        ids.append(response.json()["documento_id"])
    created = service.criar_pedido_rascunho(
        order_input(db).model_copy(update={"documento_id": UUID(ids[0])})
    )
    detail = client.get(f"/api/documentos/{ids[0]}", headers=authorization()).json()
    assert detail["status"] == "pedido_criado"
    assert detail["pedidos"][0]["documento_id"] == ids[0]
    assert detail["pedidos"][0]["pedido_id"] == str(created.pedido_id)
    matches = client.get(f"/api/pedidos?documento_id={ids[0]}", headers=authorization()).json()
    assert matches["total"] == 1
    assert matches["items"][0]["pedido_id"] == str(created.pedido_id)
    other = client.get(f"/api/pedidos?documento_id={ids[1]}", headers=authorization()).json()
    assert other["total"] == 0
    assert other["items"] == []


@pytest.mark.parametrize(
    "path, total, id_key, filter_query, expected_code",
    [
        ("/api/produtos", 100, "produto_id", "MER-001", "MER-001"),
        ("/api/clientes", 15, "cliente_id", "CLI-001", "CLI-001"),
    ],
)
def test_catalog_api_paginates_and_filters(
    document_api, path, total, id_key, filter_query, expected_code
):
    client, _ = document_api
    first = client.get(path, params={"limit": 2, "offset": 0}, headers=authorization())
    second = client.get(path, params={"limit": 2, "offset": 2}, headers=authorization())
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == total
    assert len(first.json()["items"]) == len(second.json()["items"]) == 2
    first_ids = {item[id_key] for item in first.json()["items"]}
    assert first_ids.isdisjoint(item[id_key] for item in second.json()["items"])
    filtered = client.get(path, params={"consulta": filter_query}, headers=authorization())
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    item = filtered.json()["items"][0]
    assert item.get("sku", item.get("codigo")) == expected_code
    wildcard = client.get(path, params={"consulta": "%%"}, headers=authorization())
    assert wildcard.status_code == 200
    assert wildcard.json()["total"] == 0


@pytest.mark.parametrize(
    "path", ["/api/produtos", "/api/clientes", "/api/documentos", "/api/pedidos"]
)
def test_listing_api_rejects_unbounded_pagination(document_api, path):
    client, _ = document_api
    assert client.get(path, params={"limit": 101}, headers=authorization()).status_code == 422
    assert client.get(path, params={"offset": -1}, headers=authorization()).status_code == 422
