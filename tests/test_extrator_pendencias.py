"""Pendências do esclarecimento: montagem pura e fluxo completo até a resolução humana."""

from uuid import UUID

import pytest
from extrator_apoio import busca, busca_cliente, cliente, item, produto
from test_extrator_guardrails_resolucao import ATIVOS, contexto, decisao
from test_extrator_orquestrador import CABECALHO, agente, extracao, rodar
from test_extrator_orquestrador import ambiente as ambiente
from test_integration import TEST_TOKEN
from test_integration import db as db
from test_integration import test_engine as test_engine

from extrator.guardrails_resolucao import aplicar_guardrails
from extrator.llm import ClienteLLMRoteirizado
from extrator.pendencias import montar_pendencias
from pedidos.config import Settings
from pedidos.resolucao_humana import ResolucaoService
from pedidos.schemas import Pendencias, ResolucaoInput

CAFE, SERRA = produto("MER-001"), produto("MER-002")


def test_ambiguity_lists_structured_options_and_keeps_resolved_items():
    itens = [
        item(0, "café moído de meio quilo", unidade="pacotes", quantidade=10),
        item(1, "açúcar cristal Aurora de 1 kg", quantidade=3),
    ]
    acucar = produto("MER-003")
    chamadas = [busca(0, "café moído 500 g", ATIVOS), busca(1, "açúcar", [acucar])]
    modelo = decisao(
        [
            {"idx": 0, "produto_id": CAFE["produto_id"]},
            {"idx": 1, "produto_id": acucar["produto_id"]},
        ]
    )
    ctx = contexto(itens, chamadas)
    resultado = aplicar_guardrails(modelo, ctx)
    assert resultado.decisao == "pedir_esclarecimento"
    pend = montar_pendencias(resultado, itens, "CLI-001", None, ctx.chamadas)
    Pendencias.model_validate(pend)  # formato aceito pelo backend
    ambiguo, resolvido = pend["itens"]
    assert ambiguo["situacao"] == "pendente" and ambiguo["codigo"] == "AMBIGUIDADE"
    assert {o["sku"] for o in ambiguo["opcoes"]} == {"MER-001", "MER-002"}
    assert ambiguo["quantidade"] == 10
    assert resolvido["situacao"] == "resolvido"
    assert resolvido["produto_id"] == acucar["produto_id"]
    assert [o["sku"] for o in resolvido["opcoes"]] == ["MER-003"]
    assert pend["cliente"]["situacao"] == "resolvido"
    assert pend["cliente"]["cliente_id"] == cliente("CLI-001")["cliente_id"]


def test_unknown_client_becomes_pending_with_question():
    itens = [item(0, "MER-001", sku="MER-001")]
    ctx = contexto(itens, [busca(0, "MER-001", [CAFE], "sku")], codigo="CLI-999", texto="CLI-999")
    ctx.chamadas[-1] = busca_cliente("CLI-999", [])
    resultado = aplicar_guardrails(
        decisao([{"produto_id": CAFE["produto_id"]}], cliente_id=None), ctx
    )
    pend = montar_pendencias(resultado, itens, "CLI-999", None, ctx.chamadas)
    assert pend["cliente"]["situacao"] == "pendente"
    assert "CLI-999" in pend["cliente"]["pergunta"]
    assert pend["cliente"]["opcoes"] == []
    assert pend["itens"][0]["situacao"] == "resolvido"


def test_no_pendencies_outside_clarification():
    itens = [item(0, "MER-001", sku="MER-001")]
    ctx = contexto(itens, [busca(0, "MER-001", [CAFE], "sku")])
    resultado = aplicar_guardrails(decisao([{"produto_id": CAFE["produto_id"]}]), ctx)
    assert resultado.decisao == "criar_rascunho"
    assert montar_pendencias(resultado, itens, "CLI-001", None, ctx.chamadas) is None


@pytest.mark.integration
def test_fraction_case_gets_options_without_model_and_is_resolved_by_a_person(ambiente, db):
    servidor, documentos = ambiente
    doc = documentos.criar("pedido.txt", f"{CABECALHO}MER-001, 1,5 unidades.".encode())
    llm = ClienteLLMRoteirizado([extracao("MER-001", "1,5", "MER-001, 1,5 unidades")])
    resultado = rodar(servidor, llm, agente(), doc.documento_id)
    assert resultado.decisao == "pedir_esclarecimento"
    assert len(llm.chamadas) == 1  # opções vieram de buscas de leitura, sem o modelo

    analise = documentos.obter(doc.documento_id).analises[0]
    [pendente] = analise.pendencias.itens
    assert pendente.codigo == "QUANTIDADE_FRACIONARIA"
    assert pendente.quantidade is None
    assert [o.sku for o in pendente.opcoes] == ["MER-001"]
    assert analise.pendencias.cliente.situacao == "pendente"
    assert [c.codigo for c in analise.pendencias.cliente.opcoes] == ["CLI-001"]

    # A pessoa confirma 2 unidades com o cliente e resolve na interface.
    pedido = ResolucaoService(db, Settings(api_token=TEST_TOKEN)).resolver(
        doc.documento_id,
        ResolucaoInput(
            analise_id=analise.analise_id,
            cliente_id=analise.pendencias.cliente.opcoes[0].cliente_id,
            itens=[{"item_idx": 0, "produto_id": pendente.opcoes[0].produto_id, "quantidade": 2}],
        ),
    )
    assert [(i.ean, i.quantidade) for i in pedido.itens] == [(CAFE["ean"], 2)]
    view = documentos.obter(doc.documento_id)
    assert view.status == "pedido_criado"
    assert view.analises[0].modelo == "humano"
    assert UUID(str(view.pedidos[0].pedido_id)) == pedido.pedido_id
