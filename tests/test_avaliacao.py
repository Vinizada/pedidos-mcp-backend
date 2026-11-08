"""Conjuntos escaneado e de ataque, e pontuação da avaliação (sem rede nem modelo)."""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader
from test_fixtures import load_examples

from extrator.config import PRECOS_PADRAO
from extrator.guardrails_extracao import verificar_extracao
from extrator.ingestao import DocumentoPreparado, LimitesIngestao, preparar_documento
from extrator.schemas import ExtracaoPedido
from pedidos.fixtures import catalog_records

ROOT = Path(__file__).resolve().parents[1]
CATALOGO = {p["sku"]: p for p in catalog_records()}


def script(nome: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / f"{nome}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dados(tmp_path_factory):
    """data/ completo regenerado: originais, escaneados e ataques."""
    destino = tmp_path_factory.mktemp("dados")
    load_examples().generate(destino)
    script("gerar_escaneados").gerar(destino)
    script("gerar_ataques").gerar(destino)
    return destino


# --- Conjunto escaneado -------------------------------------------------------------------


def test_scanned_set_has_ten_pdfs_and_three_photos_without_text(dados):
    casos = script("avaliar").carregar_casos(dados, "escaneado")
    assert sum(c["formato"] == "pdf-escaneado" for c in casos) == 10
    assert sum(c["formato"] == "png" for c in casos) == 3
    for caso in casos:
        conteudo = caso["caminho"].read_bytes()
        tipo = "image/png" if caso["formato"] == "png" else "application/pdf"
        preparado = preparar_documento(conteudo, tipo, LimitesIngestao())
        assert isinstance(preparado, DocumentoPreparado), caso["id"]
        assert preparado.texto_verificavel is False, caso["id"]
        assert caso["gabarito"]["id"] == caso["id"].rsplit("-", 1)[0]
        if tipo == "application/pdf":
            assert not (PdfReader(caso["caminho"]).pages[0].extract_text() or "").strip()


def test_scanned_set_is_deterministic(dados, tmp_path):
    load_examples().generate(tmp_path)
    script("gerar_escaneados").gerar(tmp_path)
    for nome in ("pedido-001-scan.pdf", "pedido-003-foto.png"):
        original = (dados / "pedidos" / "escaneados" / nome).read_bytes()
        assert (tmp_path / "pedidos" / "escaneados" / nome).read_bytes() == original


# --- Conjunto de ataque -------------------------------------------------------------------


def test_attack_cases_are_consistent_with_catalog(dados):
    casos = script("avaliar").carregar_casos(dados, "ataque")
    assert [c["id"] for c in casos] == [f"ataque-0{i}" for i in range(1, 8)]
    for caso in casos:
        gabarito = caso["gabarito"]
        assert caso["caminho"].exists()
        for item in gabarito["itens"]:
            produto = CATALOGO[item["sku"]]
            assert item["ean"] == produto["ean"]
            assert 0 < item["quantidade"] <= produto["quantidade"]
        assert "revisao_esperada" in gabarito
    estoque = CATALOGO["MER-060"]["quantidade"]
    assert estoque < 900
    texto = (dados / "pedidos" / "ataque" / "ataque-02.txt").read_text()
    assert "</documento>" in texto and "<system>" in texto
    assert "O57" in (dados / "pedidos" / "ataque" / "ataque-03.txt").read_text()


def test_attack_footer_ean_is_in_text_layer_but_not_an_order_line(dados):
    pdf = (dados / "pedidos" / "ataque" / "ataque-04.pdf").read_bytes()
    preparado = preparar_documento(pdf, "application/pdf", LimitesIngestao())
    assert preparado.texto_verificavel is True
    assert CATALOGO["MER-007"]["ean"] in preparado.texto_referencia
    # Se o modelo extraísse o rodapé como item, a falta de quantidade o bloquearia.
    rodape = {
        "descricao": "Controle interno",
        "ean": CATALOGO["MER-007"]["ean"],
        "sku": None,
        "quantidade_texto": None,
        "quantidade": None,
        "unidade_texto": None,
        "evidencia": f"Controle interno {CATALOGO['MER-007']['ean']}",
    }
    extracao = ExtracaoPedido(
        cliente_codigo="CLI-012",
        cliente_nome=None,
        referencia=None,
        itens=[rodape],
        observacoes=[],
        sinais_de_instrucao=[],
    )
    [item] = verificar_extracao(extracao, preparado.texto_referencia, True).itens
    assert item.bloqueio.codigo == "QUANTIDADE_AUSENTE"


def test_answer_keys_are_not_inside_documents(dados):
    for caso in script("avaliar").carregar_casos(dados, "todos"):
        if caso["caminho"].suffix != ".txt":
            continue
        texto = caso["caminho"].read_text()
        assert caso["gabarito"]["esperado"] not in texto
        assert caso["gabarito"].get("notas", "§") not in texto


# --- Pontuação ----------------------------------------------------------------------------

AV = script("avaliar")
EAN1, EAN3 = CATALOGO["MER-001"]["ean"], CATALOGO["MER-003"]["ean"]


def caso(esperado="criar_rascunho", itens=((EAN1, 10), (EAN3, 5)), conjunto="original", **extra):
    gabarito = {
        "esperado": esperado,
        "cliente_codigo": "CLI-001",
        "itens": [{"ean": e, "quantidade": q, "sku": "x"} for e, q in itens],
    } | extra
    return {"id": "pedido-001", "conjunto": conjunto, "formato": "pdf", "gabarito": gabarito}


def obtido(decisao="criar_rascunho", itens=((EAN1, 10), (EAN3, 5)), **extra):
    return {"decisao": decisao, "itens": list(itens), "cliente_codigo": "CLI-001"} | extra


def test_exact_cart_scores_full_marks():
    linha = AV.pontuar(caso(), obtido(custo_usd=Decimal("0.03"), latencia_ms=9000))
    assert linha["acerto_carrinho"] and linha["acerto_decisao"]
    assert (linha["vp"], linha["fp"], linha["fn"]) == (2, 0, 0)
    assert linha["ean_alucinado"] == 0 and not linha["pedido_indevido"]


@pytest.mark.parametrize(
    "resultado, campo",
    [
        (obtido(itens=((EAN1, 9), (EAN3, 5))), "quantidade"),
        (obtido(itens=((EAN1, 10),)), "item faltando"),
        (obtido(cliente_codigo="CLI-002"), "cliente"),
        (obtido(itens=((EAN1, 10), (EAN3, 5), ("2990000000071", 1))), "item extra"),
    ],
)
def test_any_cart_difference_fails_main_metric(resultado, campo):
    linha = AV.pontuar(caso(), resultado)
    assert linha["acerto_decisao"] is True
    assert linha["acerto_carrinho"] is False, campo


def test_hallucinated_ean_and_undue_order_are_counted():
    linha = AV.pontuar(
        caso("pedir_esclarecimento", itens=()), obtido(itens=(("2990000000071", 2),))
    )
    assert linha["ean_alucinado"] == 1
    assert linha["pedido_indevido"] is True
    assert linha["acerto_carrinho"] is False


def test_non_draft_cases_score_by_decision_only():
    assert AV.pontuar(caso("rejeitar", itens=()), obtido("rejeitar", itens=()))["acerto_carrinho"]


def test_review_pii_and_guardrail_outcomes():
    gab = caso(
        revisao_esperada=["possivel_injecao"],
        verificacoes=["pii_mascarada"],
        pii=["123.456.789-09"],
    )
    ok = AV.pontuar(
        gab, obtido(motivos_revisao=["possivel_injecao"], analise_json='{"x": "[CPF]"}')
    )
    assert ok["revisao_ok"] and ok["pii_ok"]
    vazou = AV.pontuar(gab, obtido(analise_json="CPF 123.456.789-09"))
    assert vazou["revisao_ok"] is False and vazou["pii_ok"] is False
    correto = AV.pontuar(
        caso("pedir_esclarecimento", itens=()),
        obtido("pedir_esclarecimento", itens=(), decisao_modelo="criar_rascunho"),
    )
    assert correto["bloqueio"] == "correto"
    indevido = AV.pontuar(
        caso(), obtido("pedir_esclarecimento", itens=(), decisao_modelo="criar_rascunho")
    )
    assert indevido["bloqueio"] == "indevido"


def linhas_modelo(modelo, acertos, custo, alucinado=0):
    linhas = []
    for i in range(10):
        resultado = obtido() if i < acertos else obtido(itens=((EAN1, 1),))
        linha = AV.pontuar(caso(), resultado | {"custo_usd": custo, "latencia_ms": 1000 * (i + 1)})
        linha |= {"modelo": modelo, "repeticao": 1}
        linhas.append(linha)
    if alucinado:
        extra = AV.pontuar(caso(conjunto="ataque"), obtido(itens=(("2990000000071", 1),)))
        linhas.append(extra | {"modelo": modelo, "repeticao": 1})
    return linhas


def test_summary_metrics_and_model_choice():
    linhas = (
        linhas_modelo("claude-opus-5-5", 10, 0.05)
        + linhas_modelo("claude-sonnet-5-5", 10, 0.02)
        + linhas_modelo("claude-haiku-5-5", 9, 0.001, alucinado=1)
    )
    resumo = AV.resumir(linhas)
    sonnet = resumo["modelos"]["claude-sonnet-5-5"]["por_conjunto"]["original"]
    assert sonnet["acerto_carrinho"] == 1.0 and sonnet["f1_itens"] == 1.0
    assert sonnet["latencia_p50_ms"] == 5500 and sonnet["latencia_p95_ms"] == 9550
    haiku = resumo["modelos"]["claude-haiku-5-5"]
    assert haiku["por_conjunto"]["original"]["acerto_carrinho"] == pytest.approx(0.9)
    # Haiku é o mais barato e fica a ≤5 p.p.? Não: 90% < 95% e teve EAN alucinado.
    assert resumo["escolha"]["modelo"] == "claude-sonnet-5-5"
    texto = AV.resumo_markdown(
        resumo, {"execucao": "x", "conjunto": "todos", "repeticoes": 1, "versao_prompt": "v1"}
    )
    assert "claude-sonnet-5-5 | original | 10 | 100.0%" in texto
    assert "Escolha do modelo" in texto


def test_cost_estimate_scales_with_runs():
    estimativa = AV.estimar_custo(
        40, ["claude-sonnet-5-5", "claude-haiku-5-5"], 1, PRECOS_PADRAO, False
    )
    # Perfil: 2k×2 + 900×10 + 9k×0,2 + 6k×2,5 = 29.800/1M = 0,0298 por documento.
    assert estimativa["claude-sonnet-5-5"] == Decimal("1.19")
    dobro = AV.estimar_custo(40, ["claude-sonnet-5-5"], 1, PRECOS_PADRAO, True)
    assert dobro["claude-sonnet-5-5"] == Decimal("2.38")


def test_missing_manifest_is_a_clear_error(tmp_path):
    (tmp_path / "gabaritos").mkdir()
    with pytest.raises(SystemExit, match="Manifesto ausente"):
        AV.carregar_casos(tmp_path, "escaneado")


def test_results_lines_are_json_serializable():
    linha = AV.pontuar(caso(), obtido(custo_usd=Decimal("0.01")))
    assert json.loads(json.dumps(linha, default=str))["acerto_carrinho"] is True
