"""Validação de entrada: o agente não controla preços ou regras comerciais."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from pedidos.schemas import BuscaInput, CarrinhoInput, ItemInput, PedidoInput, ProdutoIdsInput


def valid_order():
    return {
        "cliente_id": str(uuid4()),
        "itens": [{"produto_id": str(uuid4()), "quantidade": 2}],
        "chave_idempotencia": "documento-001-v1",
    }


@pytest.mark.parametrize("quantidade", [0, -1, 1.5, "2", True, 100_001, None])
def test_quantity_must_be_positive_bounded_integer(quantidade):
    with pytest.raises(ValidationError):
        ItemInput(produto_id=uuid4(), quantidade=quantidade)


@pytest.mark.parametrize("unidade", ["CX", "KG", "un", "PACOTE"])
def test_unsupported_unit_requires_explicit_resolution(unidade):
    with pytest.raises(ValidationError):
        ItemInput(produto_id=uuid4(), quantidade=1, unidade=unidade)


def test_agent_cannot_supply_unit_price():
    with pytest.raises(ValidationError):
        ItemInput(produto_id=uuid4(), quantidade=1, preco_unitario="0.01")


def test_agent_cannot_supply_total_or_status():
    for extra in ({"total": "0.01"}, {"status": "confirmado"}):
        with pytest.raises(ValidationError):
            PedidoInput(**valid_order(), **extra)


def test_same_product_cannot_be_counted_twice():
    product = uuid4()
    with pytest.raises(ValidationError, match="Consolide"):
        CarrinhoInput(
            cliente_id=uuid4(),
            itens=[
                {"produto_id": product, "quantidade": 2},
                {"produto_id": product, "quantidade": 3},
            ],
        )


def test_empty_and_oversized_cart_are_rejected():
    for items in ([], [{"produto_id": uuid4(), "quantidade": 1} for _ in range(101)]):
        with pytest.raises(ValidationError):
            CarrinhoInput(cliente_id=uuid4(), itens=items)


@pytest.mark.parametrize("key", ["", "curta", "chave com espacos", "a" * 101])
def test_invalid_idempotency_key_is_rejected(key):
    payload = valid_order()
    payload["chave_idempotencia"] = key
    with pytest.raises(ValidationError):
        PedidoInput(**payload)


@pytest.mark.parametrize("query", ["", " ", "a", "x" * 301])
def test_search_input_is_bounded_and_not_blank(query):
    with pytest.raises(ValidationError):
        BuscaInput(consulta=query)


@pytest.mark.parametrize("limit", [0, 21, True, "5"])
def test_search_limit_is_a_bounded_integer(limit):
    with pytest.raises(ValidationError):
        BuscaInput(consulta="café", limite=limit)


def test_availability_batch_is_bounded():
    for ids in ([], [uuid4() for _ in range(101)]):
        with pytest.raises(ValidationError):
            ProdutoIdsInput(produto_ids=ids)


def test_valid_payload_preserves_quantity_and_defaults_unit():
    order = PedidoInput(**valid_order())
    assert order.itens[0].quantidade == 2
    assert order.itens[0].unidade == "UN"
    assert order.referencia_documento is None
