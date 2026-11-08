"""Guardrails de resolução, revisão e custo: tabelas de casos, sem LLM nem banco."""

from decimal import Decimal
from uuid import uuid4

import pytest
from extrator_apoio import (
    CATALOGO,
    bloqueio,
    busca,
    busca_cliente,
    cliente,
    item,
    produto,
)
from pydantic import ValidationError

from extrator.config import AgenteSettings
from extrator.custos import ContadorCusto, OrcamentoExcedido, custo
from extrator.guardrails_resolucao import (
    ContextoResolucao,
    aplicar_guardrails,
    rebaixar_por_revalidacao,
)
from extrator.llm import Uso
from extrator.revisao import motivos_revisao
from extrator.schemas import DecisaoAgente

EAN1 = CATALOGO["MER-001"]["ean"]
DOC = "Cliente: CLI-001 - Mercado Demonstrativo Alfa\nPedido conforme itens."
CLI1 = cliente("CLI-001")


def decisao(itens, geral="criar_rascunho", cliente_id=CLI1["cliente_id"], **extra) -> DecisaoAgente:
    dados = {
        "decisao": geral,
        "cliente_id": cliente_id,
        "cliente_pergunta": None,
        "itens": [
            {
                "item_extraido_idx": i.get("idx", 0),
                "decisao": i.get("decisao", "criar_rascunho"),
                "produto_id": i.get("produto_id"),
                "quantidade": i.get("quantidade"),
                "codigo": i.get("codigo"),
                "pergunta": i.get("pergunta"),
            }
            for i in itens
        ],
        "perguntas": [],
    }
    return DecisaoAgente.model_validate(dados | extra)


def contexto(itens, chamadas, codigo="CLI-001", nome=None, texto=DOC, verificavel=True):
    clientes = [busca_cliente(codigo or nome or "x", [CLI1])]
    return ContextoResolucao(
        itens=itens,
        cliente_codigo=codigo,
        cliente_nome=nome,
        texto_referencia=texto,
        texto_verificavel=verificavel,
        chamadas=[*chamadas, *clientes],
    )


def guardrails(acionamentos):
    return {
        (a["guardrail"], a["item_idx"], a["decisao_original"], a["decisao_final"])
        for a in acionamentos
    }


# --- Caminho feliz ------------------------------------------------------------------------


def test_coded_item_resolved_by_exact_search_creates_draft():
    p = produto("MER-001", origem="ean")
    ctx = contexto([item(0, f"EAN {EAN1}", ean=EAN1, quantidade=10)], [busca(0, EAN1, [p], "ean")])
    resultado = aplicar_guardrails(decisao([{"produto_id": p["produto_id"]}]), ctx)
    assert resultado.decisao == "criar_rascunho"
    assert resultado.cliente_id == CLI1["cliente_id"]
    assert resultado.carrinho == [
        {"produto_id": p["produto_id"], "quantidade": 10, "unidade": "UN"}
    ]
    assert resultado.acionamentos == []
    assert resultado.casamento_por_descricao is False
    assert resultado.perguntas == [] and resultado.motivos == []


# --- 1. Proveniência por item -------------------------------------------------------------


def test_fabricated_product_id_is_blocked():
    p = produto("MER-001")
    ctx = contexto([item(0, f"EAN {EAN1}", ean=EAN1)], [busca(0, EAN1, [p], "ean")])
    resultado = aplicar_guardrails(decisao([{"produto_id": str(uuid4())}]), ctx)
    assert resultado.decisao == "pedir_esclarecimento"
    assert resultado.carrinho == []
    assert ("proveniencia_item", 0, "criar_rascunho", "pedir_esclarecimento") in guardrails(
        resultado.acionamentos
    )
    assert resultado.motivos[0]["codigo"] == "PRODUTO_SEM_PROVENIENCIA"


def test_product_from_another_items_search_is_blocked():
    cafe, acucar = produto("MER-001"), produto("MER-003")
    itens = [item(0, "café moído tradicional Aurora 500 g"), item(1, "açúcar cristal Aurora 1 kg")]
    chamadas = [busca(0, "café Aurora 500 g", [cafe]), busca(1, "açúcar Aurora 1 kg", [acucar])]
    modelo = decisao(
        [
            {"idx": 0, "produto_id": acucar["produto_id"]},
            {"idx": 1, "produto_id": acucar["produto_id"]},
        ]
    )
    resultado = aplicar_guardrails(modelo, contexto(itens, chamadas))
    assert resultado.itens[0].codigo == "PRODUTO_SEM_PROVENIENCIA"
    assert resultado.itens[1].decisao == "criar_rascunho"
    assert resultado.decisao == "pedir_esclarecimento"


# --- 2/3. Código exato e proibido substituir ----------------------------------------------


def test_substitution_for_nonexistent_code_becomes_rejection():
    inexistente = "2990000009999"
    outro = produto("MER-001")
    itens = [item(0, f"EAN {inexistente}", ean=inexistente)]
    chamadas = [busca(0, inexistente, [], "ean"), busca(0, "café", [outro])]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": outro["produto_id"]}]), contexto(itens, chamadas)
    )
    assert resultado.decisao == "rejeitar"
    assert resultado.motivos[0]["codigo"] == "PRODUTO_INEXISTENTE"
    assert ("proibido_substituir", 0, "criar_rascunho", "rejeitar") in guardrails(
        resultado.acionamentos
    )


@pytest.mark.parametrize(
    "sku, codigo",
    [("MER-100", "PRODUTO_INATIVO"), ("MER-099", "ESTOQUE_INSUFICIENTE")],
)
def test_unsellable_coded_product_only_allows_rejection(sku, codigo):
    p = produto(sku, origem="sku")
    itens = [item(0, sku, sku=sku, quantidade=2)]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": p["produto_id"]}]), contexto(itens, [busca(0, sku, [p], "sku")])
    )
    assert resultado.decisao == "rejeitar"
    assert resultado.motivos[0]["codigo"] == codigo


def test_model_clarification_for_missing_code_becomes_rejection_with_evidence():
    itens = [item(0, "EAN 2990000009999", ean="2990000009999")]
    modelo = decisao(
        [{"decisao": "pedir_esclarecimento", "pergunta": "Qual produto?"}],
        geral="pedir_esclarecimento",
    )
    resultado = aplicar_guardrails(modelo, contexto(itens, [busca(0, "2990000009999", [], "ean")]))
    assert resultado.decisao == "rejeitar"
    assert resultado.motivos[0]["codigo"] == "PRODUTO_INEXISTENTE"


def test_product_with_different_code_is_blocked():
    pedido, outro = produto("MER-001"), produto("MER-041")
    itens = [item(0, f"EAN {EAN1}", ean=EAN1)]
    chamadas = [busca(0, EAN1, [pedido], "ean"), busca(0, "café Aurora", [outro])]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": outro["produto_id"]}]), contexto(itens, chamadas)
    )
    assert resultado.itens[0].codigo == "PRODUTO_DIVERGENTE"
    assert ("codigo_exato", 0, "criar_rascunho", "pedir_esclarecimento") in guardrails(
        resultado.acionamentos
    )


def test_model_rejection_without_backend_code_becomes_clarification():
    p = produto("MER-001")
    itens = [item(0, "café moído tradicional Aurora 500 g")]
    modelo = decisao([{"decisao": "rejeitar", "codigo": "NAO_GOSTEI"}], geral="rejeitar")
    resultado = aplicar_guardrails(modelo, contexto(itens, [busca(0, "café", [p])]))
    assert resultado.decisao == "pedir_esclarecimento"
    assert resultado.perguntas


# --- 4/5. Atributos e ambiguidade (catálogo real) -----------------------------------------

ATIVOS = [produto(sku) for sku, p in CATALOGO.items() if p.get("ativo", True)]


@pytest.mark.parametrize(
    "descricao, unidade, sku",
    [
        ("café moído tradicional Aurora de 500 g", "pacotes", "MER-001"),
        ("açúcar cristal Aurora de 1 kg", "pacotes", "MER-003"),
        ("det. neutro Casa Serena 500 ml", "un", "MER-091"),
        ("sabão pó Casa Serena 800 g", "un", "MER-094"),
        ("Arroz branco tipo 1 Campo Claro", "pacotes de 5 kg", "MER-004"),
        ("Arroz branco tipo 1 Campo Claro", "pacotes de 1 kg", "MER-005"),
        ("bolacha maisena Forno Bom de 350 g", "pacotes", "MER-052"),
        ("massa parafuso Mesa Feliz de meio quilo", "pacotes", "MER-017"),
        ("suco integral de uva Pomar Novo de 1 L", "garrafas", "MER-074"),
        ("geleia de morango Doce Manhã de 250 g", "potes", "MER-060"),
        ("torrada tradicional Forno Bom de 160 g", "pacotes", "MER-055"),
        ("leite integral Vale Branco de 1 L", "unidades", "MER-031"),
    ],
)
def test_evaluation_descriptions_resolve_uniquely_even_against_whole_catalog(
    descricao, unidade, sku
):
    """Casos 003/004/006/007/009 do gabarito: o produto certo é o único melhor candidato."""
    escolhido = produto(sku)
    itens = [item(0, descricao, unidade=unidade)]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": escolhido["produto_id"]}]),
        contexto(itens, [busca(0, descricao, ATIVOS)]),
    )
    assert resultado.decisao == "criar_rascunho", resultado.perguntas
    assert resultado.casamento_por_descricao is True


def test_wrong_size_is_blocked():
    """Café 1 kg escolhido quando o documento pede 500 g."""
    pedido, errado = produto("MER-001"), produto("MER-042")
    itens = [item(0, "café moído tradicional Aurora de 500 g")]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": errado["produto_id"]}]),
        contexto(itens, [busca(0, "café", [pedido, errado])]),
    )
    assert resultado.itens[0].codigo == "ATRIBUTOS_DIVERGENTES"
    assert ("atributos_conferidos", 0, "criar_rascunho", "pedir_esclarecimento") in guardrails(
        resultado.acionamentos
    )


def test_two_compatible_brands_require_clarification_whatever_the_model_chose():
    """Caso 011: café moído de meio quilo, sem marca."""
    itens = [item(0, "café moído de meio quilo", unidade="pacotes", quantidade=10)]
    for escolhido in ("MER-001", "MER-002"):
        resultado = aplicar_guardrails(
            decisao([{"produto_id": produto(escolhido)["produto_id"]}]),
            contexto(itens, [busca(0, "café moído 500 g", ATIVOS)]),
        )
        assert resultado.decisao == "pedir_esclarecimento"
        assert resultado.itens[0].codigo == "AMBIGUIDADE"
        assert "Aurora" in resultado.perguntas[0] and "Serra" in resultado.perguntas[0]


def test_box_without_package_definition_requires_clarification():
    """Caso 018: "2 caixas de leite integral Vale Branco de 1 litro"."""
    itens = [item(0, "leite integral Vale Branco de 1 litro", unidade="caixas")]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": produto("MER-031")["produto_id"]}]),
        contexto(itens, [busca(0, "leite integral Vale Branco 1 L", ATIVOS)]),
    )
    assert resultado.itens[0].codigo == "AMBIGUIDADE_EMBALAGEM"


# --- 6. Cliente ---------------------------------------------------------------------------


def test_unknown_client_code_requires_clarification():
    """Caso 019: CLI-999 não existe; não inventar nem cadastrar."""
    p = produto("MER-001")
    texto = "CLI-999 - Mercado Demonstrativo Horizonte\nEAN ..."
    ctx = ContextoResolucao(
        itens=[item(0, f"EAN {EAN1}", ean=EAN1)],
        cliente_codigo="CLI-999",
        cliente_nome=None,
        texto_referencia=texto,
        texto_verificavel=True,
        chamadas=[busca(0, EAN1, [p], "ean"), busca_cliente("CLI-999", [])],
    )
    resultado = aplicar_guardrails(decisao([{"produto_id": p["produto_id"]}], cliente_id=None), ctx)
    assert resultado.decisao == "pedir_esclarecimento"
    assert any("CLI-999" in pergunta for pergunta in resultado.perguntas)
    assert ("cliente", None, "criar_rascunho", "pedir_esclarecimento") in guardrails(
        resultado.acionamentos
    )


def test_homonymous_clients_by_name_require_clarification():
    p = produto("MER-001")
    homonimo = cliente("CLI-002", nome=CLI1["nome"], cliente_id=str(uuid4()))
    ctx = ContextoResolucao(
        itens=[item(0, f"EAN {EAN1}", ean=EAN1)],
        cliente_codigo=None,
        cliente_nome=CLI1["nome"],
        texto_referencia=DOC,
        texto_verificavel=True,
        chamadas=[busca(0, EAN1, [p], "ean"), busca_cliente(CLI1["nome"], [CLI1, homonimo])],
    )
    resultado = aplicar_guardrails(decisao([{"produto_id": p["produto_id"]}]), ctx)
    assert resultado.decisao == "pedir_esclarecimento"


def test_client_code_absent_from_document_is_ignored():
    p = produto("MER-001")
    ctx = contexto(
        [item(0, f"EAN {EAN1}", ean=EAN1)], [busca(0, EAN1, [p], "ean")], texto="Pedido sem cliente"
    )
    resultado = aplicar_guardrails(decisao([{"produto_id": p["produto_id"]}]), ctx)
    assert resultado.decisao == "pedir_esclarecimento"


def test_client_must_come_from_client_search():
    p = produto("MER-001")
    ctx = contexto([item(0, f"EAN {EAN1}", ean=EAN1)], [busca(0, EAN1, [p], "ean")])
    resultado = aplicar_guardrails(
        decisao([{"produto_id": p["produto_id"]}], cliente_id=str(uuid4())), ctx
    )
    assert resultado.decisao == "pedir_esclarecimento"


# --- 7/8/9/10. Extras, cobertura, consolidação e coerência ---------------------------------


def test_extra_item_is_ignored_and_flags_review():
    p = produto("MER-001")
    ctx = contexto([item(0, f"EAN {EAN1}", ean=EAN1)], [busca(0, EAN1, [p], "ean")])
    modelo = decisao([{"produto_id": p["produto_id"]}, {"idx": 7, "produto_id": p["produto_id"]}])
    resultado = aplicar_guardrails(modelo, ctx)
    assert resultado.decisao == "criar_rascunho"
    assert resultado.carrinho[0]["quantidade"] == 2
    assert resultado.rebaixada is True
    assert ("item_extra", 7, "criar_rascunho", "criar_rascunho") in guardrails(
        resultado.acionamentos
    )


def test_missing_item_decision_requires_clarification():
    p = produto("MER-001")
    itens = [item(0, f"EAN {EAN1}", ean=EAN1), item(1, "açúcar cristal Aurora 1 kg")]
    resultado = aplicar_guardrails(
        decisao([{"produto_id": p["produto_id"]}]), contexto(itens, [busca(0, EAN1, [p], "ean")])
    )
    assert resultado.itens[1].codigo == "ITEM_NAO_RESOLVIDO"
    assert resultado.decisao == "pedir_esclarecimento"


def test_repeated_product_is_consolidated():
    p = produto("MER-001")
    itens = [
        item(0, f"EAN {EAN1}", ean=EAN1, quantidade=2),
        item(1, f"EAN {EAN1}", ean=EAN1, quantidade=3),
    ]
    chamadas = [busca(0, EAN1, [p], "ean"), busca(1, EAN1, [p], "ean")]
    modelo = decisao(
        [{"idx": 0, "produto_id": p["produto_id"]}, {"idx": 1, "produto_id": p["produto_id"]}]
    )
    resultado = aplicar_guardrails(modelo, contexto(itens, chamadas))
    assert resultado.carrinho == [{"produto_id": p["produto_id"], "quantidade": 5, "unidade": "UN"}]


def test_incoherent_general_decision_is_corrected_upwards():
    p = produto("MER-001")
    itens = [item(0, f"EAN {EAN1}", ean=EAN1), item(1, "açúcar")]
    modelo = decisao(
        [
            {"idx": 0, "produto_id": p["produto_id"]},
            {"idx": 1, "decisao": "pedir_esclarecimento", "pergunta": "Qual açúcar?"},
        ]
    )
    resultado = aplicar_guardrails(modelo, contexto(itens, [busca(0, EAN1, [p], "ean")]))
    assert resultado.decisao == "pedir_esclarecimento"
    assert resultado.perguntas == ["Qual açúcar?"]
    assert ("coerencia_decisao", None, "criar_rascunho", "pedir_esclarecimento") in guardrails(
        resultado.acionamentos
    )


def test_model_clarification_is_never_promoted_to_draft():
    p = produto("MER-001")
    ctx = contexto([item(0, f"EAN {EAN1}", ean=EAN1)], [busca(0, EAN1, [p], "ean")])
    modelo = decisao(
        [{"produto_id": p["produto_id"]}],
        geral="pedir_esclarecimento",
        perguntas=["Confirma o prazo?"],
    )
    resultado = aplicar_guardrails(modelo, ctx)
    assert resultado.decisao == "pedir_esclarecimento"
    assert resultado.perguntas == ["Confirma o prazo?"]
    assert resultado.carrinho == []


def test_stage1_blocks_prevail_without_stage2():
    itens = [
        item(0, "leite", quantidade=None, bloqueio=bloqueio()),
        item(
            1,
            "café",
            quantidade=None,
            bloqueio=bloqueio("rejeitar", "QUANTIDADE_INVALIDA", "Zero."),
        ),
    ]
    resultado = aplicar_guardrails(None, contexto(itens, []))
    assert resultado.decisao == "rejeitar"
    assert {m["codigo"] for m in resultado.motivos} == {
        "QUANTIDADE_FRACIONARIA",
        "QUANTIDADE_INVALIDA",
    }
    so_fracao = aplicar_guardrails(None, contexto(itens[:1], []))
    assert so_fracao.decisao == "pedir_esclarecimento"
    assert so_fracao.perguntas == ["Quantas?"]


def test_document_without_items_requires_clarification():
    resultado = aplicar_guardrails(None, contexto([], []))
    assert resultado.decisao == "pedir_esclarecimento"
    assert "Não identificamos itens" in resultado.perguntas[0]


# --- 11. Revalidação ----------------------------------------------------------------------


def test_backend_revalidation_problems_become_rejection():
    p = produto("MER-001")
    ctx = contexto([item(0, f"EAN {EAN1}", ean=EAN1)], [busca(0, EAN1, [p], "ean")])
    resultado = aplicar_guardrails(decisao([{"produto_id": p["produto_id"]}]), ctx)
    rebaixado = rebaixar_por_revalidacao(
        resultado,
        [
            {
                "codigo": "ESTOQUE_INSUFICIENTE",
                "mensagem": "Disponível: 1",
                "produto_id": p["produto_id"],
            }
        ],
    )
    assert rebaixado.decisao == "rejeitar"
    assert rebaixado.carrinho == []
    assert rebaixado.motivos == [
        {"codigo": "ESTOQUE_INSUFICIENTE", "mensagem": "Disponível: 1", "item_idx": 0}
    ]
    assert rebaixado.acionamentos[-1]["guardrail"] == "revalidacao_carrinho"


def test_decision_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        decisao([{"produto_id": None}], desconto="30%")


# --- Revisão obrigatória ------------------------------------------------------------------


def criado(descricao=False, rebaixado=False, quantidade=2):
    p = produto("MER-001", estoque_disponivel=10_000)
    if descricao:
        itens = [item(0, "café moído tradicional Aurora de 500 g", quantidade=quantidade)]
        chamadas = [busca(0, "café", [p])]
    else:
        itens = [item(0, f"EAN {EAN1}", ean=EAN1, quantidade=quantidade)]
        chamadas = [busca(0, EAN1, [p], "ean")]
    extras = [{"idx": 9, "produto_id": p["produto_id"]}] if rebaixado else []
    return aplicar_guardrails(
        decisao([{"produto_id": p["produto_id"]}, *extras]), contexto(itens, chamadas)
    )


@pytest.mark.parametrize(
    "resultado, extracao, total, esperado",
    [
        (criado(), [], Decimal("10"), []),
        (criado(), ["texto_nao_verificavel"], None, ["texto_nao_verificavel"]),
        (criado(descricao=True), [], None, ["casamento_por_descricao"]),
        (criado(), ["possivel_injecao"], None, ["possivel_injecao"]),
        (criado(), ["extracao_suspeita"], None, ["extracao_suspeita"]),
        (criado(), [], Decimal("5000.01"), ["total_acima_limite"]),
        (criado(quantidade=501), [], None, ["quantidade_acima_limite"]),
        (criado(rebaixado=True), [], None, ["decisao_rebaixada"]),
    ],
)
def test_review_reasons(resultado, extracao, total, esperado):
    assert motivos_revisao(resultado, extracao, total, Decimal("5000"), 500) == esperado


def test_review_only_applies_to_drafts():
    resultado = aplicar_guardrails(None, contexto([], []))
    assert motivos_revisao(resultado, ["possivel_injecao"], None, Decimal("5000"), 500) == []


# --- Custo --------------------------------------------------------------------------------


def settings(monkeypatch, **overrides):
    monkeypatch.setenv("API_TOKEN", "t" * 32)
    return AgenteSettings(_env_file=None, **overrides)


def test_cost_uses_price_table(monkeypatch):
    preco = settings(monkeypatch).preco_modelo
    uso = Uso(
        "claude-sonnet-5-5", entrada=10_000, saida=2_000, cache_leitura=5_000, cache_escrita=1_000
    )
    # 10k×2 + 2k×10 + 5k×0,2 + 1k×2,5 = 43.500 / 1M = 0,0435
    assert custo(uso, preco) == Decimal("0.043500")


def test_counter_enforces_document_ceilings_and_prices_fallback_model(monkeypatch):
    contador = ContadorCusto(settings(monkeypatch, agente_max_custo_doc_usd="0.05"))
    contador.registrar(Uso("claude-sonnet-5-5", entrada=10_000, saida=1_000))
    assert contador.usd == Decimal("0.030000")
    contador.registrar(Uso("claude-opus-5-5", entrada=1_000))  # fallback serviu a resposta
    assert contador.usd == Decimal("0.034000")
    assert contador.modelos == ["claude-sonnet-5-5", "claude-opus-5-5"]
    with pytest.raises(OrcamentoExcedido):
        contador.registrar(Uso("claude-sonnet-5-5", saida=2_000))
    tokens = ContadorCusto(settings(monkeypatch, agente_max_tokens_doc=1_000))
    with pytest.raises(OrcamentoExcedido):
        tokens.registrar(Uso("claude-sonnet-5-5", entrada=1_001))
    tokens.registrar(Uso("claude-sonnet-5-5", entrada=10), verificar=False)
