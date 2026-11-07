"""Integridade dos dados usados pelo seed e pelos exemplos do grupo."""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from pedidos.fixtures import catalog_records, client_records, synthetic_ean


def load_examples():
    script = Path(__file__).resolve().parents[1] / "scripts" / "gerar_exemplos.py"
    spec = importlib.util.spec_from_file_location("gerar_exemplos", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_has_unique_valid_synthetic_codes_and_realistic_prices():
    products = catalog_records()
    assert len(products) == 100
    assert len({p["ean"] for p in products}) == 100
    assert len({p["sku"] for p in products}) == 100
    for product in products:
        ean = product["ean"]
        assert len(ean) == 13 and ean.isdigit()
        assert sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(ean)) % 10 == 0
        assert Decimal(product["preco"]) > 0
        assert product["quantidade"] >= 0
        assert product["unidade"] == "UN"
    assert {p["sku"] for p in products if p["quantidade"] == 0} == {"MER-099"}
    assert {p["sku"] for p in products if not p["ativo"]} == {"MER-100"}


def test_fixtures_are_repeatable_and_return_independent_records():
    products = catalog_records()
    products[0]["nome"] = "alterado"
    assert catalog_records()[0]["nome"] != "alterado"
    assert catalog_records() == catalog_records()
    assert client_records() == client_records()
    assert len(client_records()) == 15
    assert all(c["email"].endswith("@example.invalid") for c in client_records())


def test_expected_success_cases_can_be_fulfilled_by_seed():
    module = load_examples()
    cases = module.example_cases()
    products = {p["sku"]: p for p in catalog_records()}
    clients = {c["codigo"] for c in client_records()}
    assert len(cases) == 20
    assert len({c["id"] for c in cases}) == 20
    assert sum(c["formato"] == "pdf" for c in cases) == 10
    assert sum(c["formato"] == "txt" for c in cases) == 10
    assert {c["esperado"] for c in cases} == {"criar_rascunho", "pedir_esclarecimento", "rejeitar"}
    for case in cases:
        assert "esperado" not in module.document_text(case)
        if case["esperado"] != "criar_rascunho":
            assert case["itens"] == []
            continue
        assert case["cliente_codigo"] in clients
        assert case["itens"]
        for item in case["itens"]:
            product = products[item["sku"]]
            assert item["ean"] == product["ean"]
            assert product["ativo"]
            assert 0 < item["quantidade"] <= product["quantidade"]


def test_document_text_does_not_reveal_evaluation_labels():
    module = load_examples()
    for case in module.example_cases():
        document = module.document_text(case)
        assert module.DOCUMENT_TITLE in document
        assert case["titulo"] not in document
        assert case["esperado"] not in document
        assert case["notas"] not in document
        assert case["conteudo"] in document


@pytest.mark.parametrize("value", [0, -1, 1000000])
def test_synthetic_ean_rejects_out_of_range(value):
    with pytest.raises(ValueError):
        synthetic_ean(value)
