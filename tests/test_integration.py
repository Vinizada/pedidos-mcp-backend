"""Regras comerciais contra PostgreSQL real, sem agente nem modelo generativo.

Execute somente com TEST_DATABASE_URL apontando para banco descartável cujo nome
termine em _test. Migrações são aplicadas; cada teste roda em transação revertida.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pedidos.config import Settings
from pedidos.models import Cliente, Estoque, Pedido, PedidoItem, Preco, Produto
from pedidos.schemas import BuscaInput, CarrinhoInput, PedidoInput, ProdutoIdsInput

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]
TEST_TOKEN = "t" * 32


@pytest.fixture(scope="module")
def test_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Defina TEST_DATABASE_URL para executar a integração PostgreSQL.")
    parsed = make_url(url)
    if not parsed.drivername.startswith("postgresql") or not (parsed.database or "").endswith(
        "_test"
    ):
        pytest.fail("Use um PostgreSQL descartável cujo nome termine em _test.")
    env = os.environ | {
        "DATABASE_URL": url,
        "API_TOKEN": TEST_TOKEN,
        "SEARCH_MODE": "lexical",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine):
    from pedidos.seed import seed_catalog

    with test_engine.connect() as connection:
        outer = connection.begin()
        with Session(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            seed_catalog(session)
            session.commit()
            yield session
        outer.rollback()


@pytest.fixture
def service(db):
    from pedidos.service import CatalogService

    settings = Settings(api_token=TEST_TOKEN, search_mode="lexical")
    return CatalogService(db, settings)


def order_input(db, *, quantity=2, products=1, key=None):
    available = list(
        db.scalars(
            select(Produto)
            .join(Estoque)
            .where(Produto.ativo.is_(True), Estoque.quantidade - Estoque.reservado >= quantity)
            .order_by(Produto.sku)
            .limit(products)
        )
    )
    assert len(available) == products
    client = db.scalars(
        select(Cliente).where(Cliente.ativo.is_(True)).order_by(Cliente.codigo)
    ).first()
    return PedidoInput(
        cliente_id=client.id,
        itens=[{"produto_id": product.id, "quantidade": quantity} for product in available],
        chave_idempotencia=key or f"test-{uuid4()}",
        referencia_documento="fixture-pedido.txt",
    )


def cart_input(order):
    return CarrinhoInput(cliente_id=order.cliente_id, itens=order.itens)


def assert_invalid(result, code):
    assert result.valido is False
    assert result.total is None
    assert code in {problem.codigo for problem in result.problemas}


def count_orders(db):
    return db.scalar(select(func.count()).select_from(Pedido))


def test_seed_is_repeatable_and_does_not_reset_business_data(db):
    from pedidos.seed import seed_catalog

    assert db.scalar(select(func.count()).select_from(Produto)) == 100
    assert db.scalar(select(func.count()).select_from(Cliente)) == 15
    stock = db.scalars(select(Estoque).where(Estoque.quantidade > 0)).first()
    stock.quantidade = 7
    db.commit()
    seed_catalog(db)
    db.flush()
    assert db.scalar(select(func.count()).select_from(Produto)) == 100
    assert db.scalar(select(func.count()).select_from(Preco)) == 100
    assert stock.quantidade == 7


def test_ean_lookup_is_exact_and_unknown_ean_does_not_fallback(service, db):
    product = db.scalars(select(Produto).where(Produto.ativo.is_(True))).first()
    result = service.buscar_produtos(BuscaInput(consulta=product.ean))
    assert len(result.candidatos) == 1
    assert result.candidatos[0].produto_id == product.id
    assert result.candidatos[0].origem == "ean"
    assert service.buscar_produtos(BuscaInput(consulta="0000000000000")).candidatos == []


def test_lexical_search_finds_catalog_product(service):
    result = service.buscar_produtos(BuscaInput(consulta="café", limite=5))
    assert result.candidatos
    assert len(result.candidatos) <= 5
    assert all(candidate.origem == "lexical" for candidate in result.candidatos)


def test_availability_reports_unknown_ids(service, db):
    known = db.scalars(select(Produto)).first()
    unknown = uuid4()
    result = service.consultar_disponibilidade(ProdutoIdsInput(produto_ids=[known.id, unknown]))
    assert [product.produto_id for product in result.produtos] == [known.id]
    assert result.nao_encontrados == [unknown]


def test_client_code_lookup_returns_specific_client(service, db):
    client = db.scalars(select(Cliente).order_by(Cliente.codigo)).first()
    result = service.buscar_cliente(BuscaInput(consulta=client.codigo))
    assert len(result.clientes) == 1
    assert result.clientes[0].cliente_id == client.id


def test_client_search_treats_like_wildcards_as_literal_text(service):
    assert service.buscar_cliente(BuscaInput(consulta="%%")).clientes == []
    assert service.buscar_cliente(BuscaInput(consulta="__")).clientes == []


def test_server_computes_prices_and_totals(service, db):
    order = order_input(db, products=2)
    result = service.validar_carrinho(cart_input(order))
    assert result.valido is True
    assert result.problemas == []
    assert len(result.itens) == 2
    expected_total = Decimal("0.00")
    for item in result.itens:
        price = db.scalar(select(Preco.valor).where(Preco.produto_id == item.produto_id))
        assert item.preco_unitario == price
        assert item.subtotal == price * 2
        expected_total += price * 2
    assert result.total == expected_total
    assert count_orders(db) == 0


def test_unknown_client_is_rejected(service, db):
    order = order_input(db).model_copy(update={"cliente_id": uuid4()})
    assert_invalid(service.validar_carrinho(cart_input(order)), "CLIENTE_INVALIDO")


def test_unknown_product_is_rejected(service, db):
    order = order_input(db)
    order.itens[0].produto_id = uuid4()
    assert_invalid(service.validar_carrinho(cart_input(order)), "PRODUTO_INEXISTENTE")


def test_inactive_product_is_rejected(service, db):
    order = order_input(db)
    db.get(Produto, order.itens[0].produto_id).ativo = False
    db.flush()
    assert_invalid(service.validar_carrinho(cart_input(order)), "PRODUTO_INATIVO")


def test_product_without_current_price_is_rejected(service, db):
    order = order_input(db)
    db.execute(delete(Preco).where(Preco.produto_id == order.itens[0].produto_id))
    db.flush()
    assert_invalid(service.validar_carrinho(cart_input(order)), "SEM_PRECO")


def test_future_price_does_not_count_as_current(service, db):
    order = order_input(db)
    price = db.scalars(select(Preco).where(Preco.produto_id == order.itens[0].produto_id)).one()
    price.inicio_vigencia = datetime.now(UTC) + timedelta(days=1)
    db.flush()
    assert_invalid(service.validar_carrinho(cart_input(order)), "SEM_PRECO")


def test_database_rejects_overlapping_price_periods(db):
    order = order_input(db)
    product_id = order.itens[0].produto_id
    with pytest.raises(IntegrityError) as raised, db.begin_nested():
        db.add(
            Preco(
                produto_id=product_id,
                valor=Decimal("9.99"),
                inicio_vigencia=datetime.now(UTC),
                fim_vigencia=None,
            )
        )
        db.flush()
    assert raised.value.orig.diag.constraint_name == "ex_precos_vigencia"


def test_reserved_stock_is_not_available(service, db):
    order = order_input(db, quantity=2)
    stock = db.get(Estoque, order.itens[0].produto_id)
    stock.quantidade = 10
    stock.reservado = 9
    db.flush()
    assert_invalid(service.validar_carrinho(cart_input(order)), "ESTOQUE_INSUFICIENTE")


def test_invalid_cart_creates_neither_header_nor_items(service, db):
    from pedidos.service import DomainError

    order = order_input(db, products=2)
    order.itens[1].produto_id = uuid4()
    before = count_orders(db)
    with pytest.raises(DomainError) as raised:
        service.criar_pedido_rascunho(order)
    assert raised.value.codigo == "CARRINHO_INVALIDO"
    assert count_orders(db) == before
    assert db.scalar(select(func.count()).select_from(PedidoItem)) == 0


def test_draft_creation_preserves_stock_and_uses_price_snapshot(service, db):
    order = order_input(db)
    product_id = order.itens[0].produto_id
    stock = db.get(Estoque, product_id)
    initial_stock = (stock.quantidade, stock.reservado)
    created = service.criar_pedido_rascunho(order)
    assert created.status == "rascunho"
    assert created.repeticao_idempotente is False
    original_price = created.itens[0].preco_unitario
    original_name = created.itens[0].nome_produto
    price = db.scalars(select(Preco).where(Preco.produto_id == product_id)).one()
    price.valor += Decimal("5.00")
    db.get(Produto, product_id).nome = "Nome alterado depois do pedido"
    db.commit()
    loaded = service.consultar_pedido(created.pedido_id)
    assert loaded.itens[0].preco_unitario == original_price
    assert loaded.itens[0].nome_produto == original_name
    assert loaded.total == created.total
    db.refresh(stock)
    assert (stock.quantidade, stock.reservado) == initial_stock


def test_repeating_operation_returns_same_order_even_if_catalog_changed(service, db):
    order = order_input(db)
    created = service.criar_pedido_rascunho(order)
    db.get(Produto, order.itens[0].produto_id).ativo = False
    db.commit()
    repeated = service.criar_pedido_rascunho(order)
    assert repeated.pedido_id == created.pedido_id
    assert repeated.repeticao_idempotente is True
    assert repeated.total == created.total
    assert count_orders(db) == 1


def test_item_order_does_not_change_idempotency(service, db):
    order = order_input(db, products=2)
    created = service.criar_pedido_rascunho(order)
    reordered = order.model_copy(update={"itens": list(reversed(order.itens))})
    repeated = service.criar_pedido_rascunho(reordered)
    assert repeated.pedido_id == created.pedido_id
    assert repeated.repeticao_idempotente is True
    assert count_orders(db) == 1


def test_same_key_with_different_content_is_conflict(service, db):
    from pedidos.service import DomainError

    order = order_input(db)
    created = service.criar_pedido_rascunho(order)
    changed = order.model_copy(deep=True)
    changed.itens[0].quantidade += 1
    with pytest.raises(DomainError) as raised:
        service.criar_pedido_rascunho(changed)
    assert raised.value.codigo == "IDEMPOTENCIA_CONFLITO"
    assert count_orders(db) == 1
    assert service.consultar_pedido(created.pedido_id).itens[0].quantidade == 2


def test_unknown_order_is_domain_error(service):
    from pedidos.service import DomainError

    with pytest.raises(DomainError) as raised:
        service.consultar_pedido(uuid4())
    assert raised.value.codigo == "PEDIDO_NAO_ENCONTRADO"


def test_database_rejects_ean_with_invalid_check_digit(db):
    product = db.scalars(select(Produto).order_by(Produto.sku)).first()
    wrong_digit = str((int(product.ean[-1]) + 1) % 10)
    with pytest.raises(IntegrityError) as raised, db.begin_nested():
        product.ean = product.ean[:-1] + wrong_digit
        db.flush()
    assert raised.value.orig.diag.constraint_name == "ck_produtos_ean_checksum"


def test_database_rejects_stock_for_unknown_product(db):
    with pytest.raises(IntegrityError) as raised, db.begin_nested():
        db.execute(insert(Estoque).values(produto_id=uuid4(), quantidade=10, reservado=0))
    assert raised.value.orig.sqlstate == "23503"  # foreign_key_violation


def test_database_prevents_deleting_referenced_product(db):
    product = db.scalars(select(Produto).order_by(Produto.sku)).first()
    with pytest.raises(IntegrityError) as raised, db.begin_nested():
        db.execute(delete(Produto).where(Produto.id == product.id))
    assert raised.value.orig.sqlstate == "23503"


def test_database_rejects_reservation_above_total_stock(db):
    stock = db.scalars(select(Estoque)).first()
    with pytest.raises(IntegrityError) as raised, db.begin_nested():
        stock.reservado = stock.quantidade + 1
        db.flush()
    assert raised.value.orig.diag.constraint_name == "ck_estoque_reservado"


def test_database_prevents_duplicate_stock_row(db):
    stock = db.scalars(select(Estoque)).first()
    with pytest.raises(IntegrityError) as raised, db.begin_nested():
        db.execute(insert(Estoque).values(produto_id=stock.produto_id, quantidade=1, reservado=0))
    assert raised.value.orig.sqlstate == "23505"  # unique_violation (PK)
