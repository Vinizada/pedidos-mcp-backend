"""Idempotência concorrente em conexões PostgreSQL independentes.

Somente banco descartável com nome terminado em `_test`. Cada operação usa chave
aleatória; a limpeza remove apenas os pedidos identificados por essas chaves.
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pedidos.config import Settings
from pedidos.models import Cliente, Estoque, Pedido, PedidoItem, Produto
from pedidos.schemas import PedidoInput
from pedidos.seed import seed_catalog
from pedidos.service import CatalogService, DomainError

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]
TEST_TOKEN = "c" * 32


@pytest.fixture(scope="module")
def race_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Defina TEST_DATABASE_URL para testar concorrência PostgreSQL.")
    parsed = make_url(url)
    if not parsed.drivername.startswith("postgresql") or not (parsed.database or "").endswith(
        "_test"
    ):
        pytest.fail("Use um PostgreSQL descartável cujo nome termine em _test.")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=os.environ | {"DATABASE_URL": url, "API_TOKEN": TEST_TOKEN, "SEARCH_MODE": "lexical"},
        capture_output=True,
        check=True,
        text=True,
    )
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"options": "-c statement_timeout=10000 -c lock_timeout=10000"},
    )
    with Session(engine) as session:
        seed_catalog(session)
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def race_order(race_engine):
    key = f"concurrency-{uuid4()}"
    with Session(race_engine) as session:
        product_id = session.scalar(
            select(Produto.id)
            .join(Estoque)
            .where(Produto.ativo.is_(True), Estoque.quantidade - Estoque.reservado >= 2)
            .order_by(Produto.sku)
            .limit(1)
        )
        client_id = session.scalar(
            select(Cliente.id).where(Cliente.ativo.is_(True)).order_by(Cliente.codigo).limit(1)
        )
    assert product_id is not None and client_id is not None
    order = PedidoInput(
        cliente_id=client_id,
        itens=[{"produto_id": product_id, "quantidade": 1}],
        chave_idempotencia=key,
        referencia_documento="teste-concorrencia.txt",
    )
    try:
        yield order
    finally:
        # Nunca limpa o catálogo nem pedidos de outras operações/testes.
        with race_engine.begin() as connection:
            ids = list(
                connection.scalars(select(Pedido.id).where(Pedido.chave_idempotencia == key))
            )
            if ids:
                connection.execute(delete(PedidoItem).where(PedidoItem.pedido_id.in_(ids)))
                connection.execute(delete(Pedido).where(Pedido.id.in_(ids)))


def concurrently_create(engine, orders):
    barrier = Barrier(len(orders), timeout=10)
    settings = Settings(api_token=TEST_TOKEN, search_mode="lexical")

    def worker(order):
        with Session(engine, expire_on_commit=False) as session:
            # Reserva uma conexão distinta antes de liberar as duas operações.
            session.connection()
            barrier.wait()
            try:
                return CatalogService(session, settings).criar_pedido_rascunho(order)
            except DomainError as error:
                return error

    with ThreadPoolExecutor(max_workers=len(orders)) as executor:
        futures = [executor.submit(worker, order) for order in orders]
        return [future.result(timeout=30) for future in futures]


def test_concurrent_retries_create_exactly_one_order(race_engine, race_order):
    results = concurrently_create(race_engine, [race_order, race_order.model_copy(deep=True)])
    assert not any(isinstance(result, DomainError) for result in results)
    assert len({result.pedido_id for result in results}) == 1
    assert sorted(result.repeticao_idempotente for result in results) == [False, True]
    assert results[0].total == results[1].total
    with Session(race_engine) as session:
        ids = list(
            session.scalars(
                select(Pedido.id).where(Pedido.chave_idempotencia == race_order.chave_idempotencia)
            )
        )
        assert ids == [results[0].pedido_id]
        assert (
            session.scalar(
                select(func.count()).select_from(PedidoItem).where(PedidoItem.pedido_id == ids[0])
            )
            == 1
        )


def test_concurrent_different_payloads_with_same_key_conflict(race_engine, race_order):
    changed = race_order.model_copy(deep=True)
    changed.itens[0].quantidade = 2
    results = concurrently_create(race_engine, [race_order, changed])
    successful = [result for result in results if not isinstance(result, DomainError)]
    errors = [result for result in results if isinstance(result, DomainError)]
    assert len(successful) == 1
    assert len(errors) == 1
    assert errors[0].codigo == "IDEMPOTENCIA_CONFLITO"
    assert successful[0].repeticao_idempotente is False
    with Session(race_engine) as session:
        ids = list(
            session.scalars(
                select(Pedido.id).where(Pedido.chave_idempotencia == race_order.chave_idempotencia)
            )
        )
        assert ids == [successful[0].pedido_id]
        item = session.scalars(select(PedidoItem).where(PedidoItem.pedido_id == ids[0])).one()
        assert item.quantidade == successful[0].itens[0].quantidade
