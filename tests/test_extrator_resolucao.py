"""Loop de ferramentas da Etapa 2 com modelo roteirizado e MCP falso (sem rede)."""

import asyncio
import json

import pytest
from extrator_apoio import cliente, item, produto

from extrator.config import AgenteSettings
from extrator.custos import ContadorCusto, OrcamentoExcedido
from extrator.llm import BlocoToolUse, ClienteLLMRoteirizado, resposta_ferramentas
from extrator.mcp_cliente import FERRAMENTAS_LLM, ErroFerramenta, FerramentaNaoPermitida
from extrator.resolucao import CONCLUIR, LimiteVoltas, ferramentas_etapa2, resolver

P1 = produto("MER-001")
C1 = cliente("CLI-001")


class FakeMCP:
    def __init__(self, respostas=None, erros=None):
        self.respostas = respostas or {}
        self.erros = erros or {}
        self.chamadas = []

    async def ferramentas_llm(self):
        return [
            {
                "name": nome,
                "description": nome,
                "input_schema": {
                    "type": "object",
                    "properties": {"entrada": {"type": "object", "additionalProperties": False}},
                    "required": ["entrada"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
            for nome in FERRAMENTAS_LLM
        ]

    async def chamar_para_llm(self, nome, argumentos):
        if nome not in FERRAMENTAS_LLM:
            raise FerramentaNaoPermitida(nome)
        self.chamadas.append((nome, argumentos))
        if nome in self.erros:
            raise self.erros[nome]
        return self.respostas.get(nome, {})


def settings(monkeypatch, **overrides):
    monkeypatch.setenv("API_TOKEN", "t" * 32)
    return AgenteSettings(_env_file=None, **overrides)


def concluir(id_="c1", **dados):
    entrada = {
        "decisao": "criar_rascunho",
        "cliente_id": C1["cliente_id"],
        "cliente_pergunta": None,
        "itens": [
            {
                "item_extraido_idx": 0,
                "decisao": "criar_rascunho",
                "produto_id": P1["produto_id"],
                "quantidade": 2,
                "codigo": None,
                "pergunta": None,
            }
        ],
        "perguntas": [],
    } | dados
    return BlocoToolUse(CONCLUIR, entrada, id_)


def executar(passos, monkeypatch, mcp=None, itens=None, max_voltas=5, **settings_extra):
    llm = ClienteLLMRoteirizado(passos)
    mcp = mcp or FakeMCP(
        {"buscar_produtos": {"consulta": "MER-001", "modo": "sku", "candidatos": [P1]},
         "buscar_cliente": {"clientes": [C1]}}
    )  # fmt: skip
    contador = ContadorCusto(settings(monkeypatch, **settings_extra))
    resultado = asyncio.run(
        resolver(
            llm,
            mcp,
            itens or [item(0, "MER-001", sku="MER-001")],
            "CLI-001",
            None,
            contador,
            max_voltas,
        )
    )
    return resultado, llm, mcp


def test_tool_definitions_add_item_idx_and_strict_conclusion():
    ferramentas = ferramentas_etapa2(asyncio.run(FakeMCP().ferramentas_llm()))
    nomes = [f["name"] for f in ferramentas]
    assert nomes == [*FERRAMENTAS_LLM, CONCLUIR]
    assert not {"criar_pedido_rascunho", "registrar_analise"} & set(nomes)
    busca = ferramentas[0]["input_schema"]
    assert busca["properties"]["item_idx"]["type"] == "integer"
    assert "item_idx" in busca["required"]
    assert "item_idx" not in ferramentas[1]["input_schema"]["properties"]
    assert ferramentas[-1]["strict"] is True
    assert ferramentas[-1]["input_schema"]["additionalProperties"] is False


def test_loop_records_provenance_and_strips_item_idx(monkeypatch):
    passos = [
        resposta_ferramentas(
            BlocoToolUse(
                "buscar_produtos", {"item_idx": 0, "entrada": {"consulta": "MER-001"}}, "t1"
            ),
            BlocoToolUse("buscar_cliente", {"entrada": {"consulta": "CLI-001"}}, "t2"),
        ),
        resposta_ferramentas(concluir()),
    ]
    resultado, llm, mcp = executar(passos, monkeypatch)
    assert resultado.voltas == 2
    assert str(resultado.decisao.itens[0].produto_id) == P1["produto_id"]
    assert mcp.chamadas == [
        ("buscar_produtos", {"entrada": {"consulta": "MER-001"}}),
        ("buscar_cliente", {"entrada": {"consulta": "CLI-001"}}),
    ]
    assert [(c.ferramenta, c.alvo) for c in resultado.chamadas] == [
        ("buscar_produtos", 0),
        ("buscar_cliente", "cliente"),
    ]
    # Resultados das chamadas paralelas voltam juntos numa única mensagem do usuário.
    segunda = llm.chamadas[1]["messages"]
    assert [b["tool_use_id"] for b in segunda[-1]["content"]] == ["t1", "t2"]
    assert json.loads(segunda[-1]["content"][0]["content"])["candidatos"][0]["sku"] == "MER-001"
    # A primeira mensagem traz só os itens verificados, nunca o documento.
    texto = llm.chamadas[0]["messages"][0]["content"][0]["text"]
    assert texto.startswith("<pedido_extraido>") and '"idx": 0' in texto


@pytest.mark.parametrize("item_idx", [5, "0", None, True])
def test_invalid_item_idx_is_refused_without_calling_backend(monkeypatch, item_idx):
    argumentos = {"entrada": {"consulta": "MER-001"}} | (
        {"item_idx": item_idx} if item_idx is not None else {}
    )
    passos = [
        resposta_ferramentas(BlocoToolUse("buscar_produtos", argumentos, "t1")),
        resposta_ferramentas(concluir()),
    ]
    resultado, llm, mcp = executar(passos, monkeypatch)
    assert mcp.chamadas == []
    assert resultado.chamadas == []
    erro = llm.chamadas[1]["messages"][-1]["content"][0]
    assert erro["is_error"] is True and "ITEM_IDX_INVALIDO" in erro["content"]


def test_write_tool_requested_by_model_is_refused(monkeypatch):
    passos = [
        resposta_ferramentas(BlocoToolUse("criar_pedido_rascunho", {"entrada": {}}, "t1")),
        resposta_ferramentas(concluir()),
    ]
    _, llm, mcp = executar(passos, monkeypatch)
    assert mcp.chamadas == []
    erro = llm.chamadas[1]["messages"][-1]["content"][0]
    assert erro["is_error"] is True and "FERRAMENTA_NAO_PERMITIDA" in erro["content"]


def test_backend_error_is_returned_to_model_and_recorded(monkeypatch):
    mcp = FakeMCP(
        erros={"validar_carrinho": ErroFerramenta("validar_carrinho", "ARGUMENTOS_INVALIDOS", "x")}
    )
    passos = [
        resposta_ferramentas(BlocoToolUse("validar_carrinho", {"entrada": {}}, "t1")),
        resposta_ferramentas(concluir()),
    ]
    resultado, llm, _ = executar(passos, monkeypatch, mcp=mcp)
    assert resultado.chamadas[0].erro["codigo"] == "ARGUMENTOS_INVALIDOS"
    assert llm.chamadas[1]["messages"][-1]["content"][0]["is_error"] is True


def test_text_answer_gets_a_reminder_and_loop_continues(monkeypatch):
    passos = ["Vou pensar.", resposta_ferramentas(concluir())]
    resultado, llm, _ = executar(passos, monkeypatch)
    assert resultado.voltas == 2
    assert "concluir_analise" in llm.chamadas[1]["messages"][-1]["content"][0]["text"]


def test_invalid_conclusion_is_returned_as_error(monkeypatch):
    invalida = BlocoToolUse(CONCLUIR, {"decisao": "confirmar"}, "c0")
    resultado, llm, _ = executar(
        [resposta_ferramentas(invalida), resposta_ferramentas(concluir())], monkeypatch
    )
    assert resultado.voltas == 2
    assert "DECISAO_INVALIDA" in llm.chamadas[1]["messages"][-1]["content"][0]["content"]


def test_loop_stops_at_turn_limit(monkeypatch):
    passos = ["sem ferramenta"] * 3
    with pytest.raises(LimiteVoltas):
        executar(passos, monkeypatch, max_voltas=3)


def test_document_cost_ceiling_interrupts_loop(monkeypatch):
    passos = [
        resposta_ferramentas(BlocoToolUse("buscar_cliente", {"entrada": {"consulta": "x"}}, "t"))
    ] * 3
    with pytest.raises(OrcamentoExcedido):
        executar(passos, monkeypatch, agente_max_tokens_doc=3_000)
