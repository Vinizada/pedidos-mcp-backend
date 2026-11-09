"""Etapa 2: loop de ferramentas. O modelo só lê o catálogo; a ponte registra a proveniência.

O documento original não é reenviado nesta etapa: o modelo recebe apenas os itens já
verificados, o que reduz custo e superfície de injeção.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import ValidationError

from extrator.custos import ContadorCusto
from extrator.guardrails_extracao import ItemVerificado
from extrator.guardrails_resolucao import RegistroChamada
from extrator.llm import ClienteLLM
from extrator.mcp_cliente import ClienteMCP, ErroFerramenta, FerramentaNaoPermitida
from extrator.prompts import (
    DESCRICAO_CONCLUIR,
    DESCRICAO_ITEM_IDX,
    LEMBRETE_CONCLUIR,
    SYSTEM_RESOLUCAO,
    mensagem_resolucao,
)
from extrator.schemas import DecisaoAgente

MAX_TOKENS_VOLTA = 16_000
CONCLUIR = "concluir_analise"


class LimiteVoltas(Exception):
    pass


@dataclass
class ResultadoEtapa2:
    decisao: DecisaoAgente
    chamadas: list[RegistroChamada] = field(default_factory=list)
    voltas: int = 0


def ferramentas_etapa2(definicoes_mcp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ferramentas MCP de leitura (com `item_idx` na busca de produtos) + `concluir_analise`."""
    ferramentas = []
    for definicao in definicoes_mcp:
        definicao = json.loads(json.dumps(definicao))  # cópia profunda
        if definicao["name"] == "buscar_produtos":
            schema = definicao["input_schema"]
            schema["properties"]["item_idx"] = {
                "type": "integer",
                "description": DESCRICAO_ITEM_IDX,
            }
            schema["required"] = [*schema.get("required", []), "item_idx"]
        ferramentas.append(definicao)
    ferramentas.append(
        {
            "name": CONCLUIR,
            "description": DESCRICAO_CONCLUIR,
            "input_schema": anthropic.transform_schema(DecisaoAgente),
            "strict": True,
        }
    )
    return ferramentas


def pedido_para_modelo(
    itens: list[ItemVerificado], cliente_codigo: str | None, cliente_nome: str | None
) -> dict[str, Any]:
    return {
        "cliente": {"codigo": cliente_codigo, "nome": cliente_nome},
        "itens": [
            {
                "idx": item.idx,
                "descricao": item.descricao,
                "ean": item.ean,
                "sku": item.sku,
                "quantidade": item.quantidade,
                "embalagem_pedida": item.unidade_texto,
            }
            for item in itens
        ],
    }


def _resultado(tool_use_id: str, conteudo: Any, erro: bool = False) -> dict[str, Any]:
    bloco = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(conteudo, ensure_ascii=False, default=str),
    }
    if erro:
        bloco["is_error"] = True
    return bloco


class Ponte:
    """Executa as chamadas pedidas pelo modelo e guarda cada uma com seu alvo."""

    def __init__(self, mcp: ClienteMCP, indices_validos: set[int]):
        self.mcp = mcp
        self.indices = indices_validos
        self.chamadas: list[RegistroChamada] = []

    async def executar(self, bloco: Any) -> dict[str, Any]:
        argumentos = dict(bloco.input or {})
        alvo: int | str | None = None
        if bloco.name == "buscar_produtos":
            alvo = argumentos.pop("item_idx", None)
            if not isinstance(alvo, int) or isinstance(alvo, bool) or alvo not in self.indices:
                erro = {
                    "codigo": "ITEM_IDX_INVALIDO",
                    "mensagem": f"Use um idx entre {sorted(self.indices)}.",
                }
                return _resultado(bloco.id, erro, erro=True)
        elif bloco.name == "buscar_cliente":
            alvo = "cliente"
        try:
            resultado = await self.mcp.chamar_para_llm(bloco.name, argumentos)
        except FerramentaNaoPermitida as exc:
            return _resultado(
                bloco.id, {"codigo": "FERRAMENTA_NAO_PERMITIDA", "mensagem": str(exc)}, True
            )
        except ErroFerramenta as exc:
            self.chamadas.append(
                RegistroChamada(bloco.name, alvo, argumentos, erro=exc.como_dict())
            )
            return _resultado(bloco.id, exc.como_dict(), erro=True)
        self.chamadas.append(RegistroChamada(bloco.name, alvo, argumentos, resultado=resultado))
        return _resultado(bloco.id, resultado)


async def resolver(
    llm: ClienteLLM,
    mcp: ClienteMCP,
    itens: list[ItemVerificado],
    cliente_codigo: str | None,
    cliente_nome: str | None,
    contador: ContadorCusto,
    max_voltas: int,
) -> ResultadoEtapa2:
    ferramentas = ferramentas_etapa2(await mcp.ferramentas_llm())
    ponte = Ponte(mcp, {item.idx for item in itens})
    pedido = pedido_para_modelo(itens, cliente_codigo, cliente_nome)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": mensagem_resolucao(pedido)}]}
    ]
    for volta in range(1, max_voltas + 1):
        resposta = await llm.conversar(
            system=SYSTEM_RESOLUCAO,
            messages=messages,
            tools=ferramentas,
            max_tokens=MAX_TOKENS_VOLTA,
        )
        contador.registrar(resposta.uso)
        # O conteúdo completo volta ao histórico (inclui blocos de thinking).
        messages.append({"role": "assistant", "content": resposta.conteudo})
        usos = [b for b in resposta.conteudo if getattr(b, "type", None) == "tool_use"]
        if not usos:
            messages.append(
                {"role": "user", "content": [{"type": "text", "text": LEMBRETE_CONCLUIR}]}
            )
            continue
        resultados = []
        conclusao = None
        for bloco in usos:
            if bloco.name == CONCLUIR:
                try:
                    conclusao = DecisaoAgente.model_validate(bloco.input)
                except ValidationError as exc:
                    erro = {
                        "codigo": "DECISAO_INVALIDA",
                        "mensagem": f"{exc.error_count()} erro(s) de formato.",
                    }
                    resultados.append(_resultado(bloco.id, erro, erro=True))
                continue
            resultados.append(await ponte.executar(bloco))
        if conclusao is not None:
            # Chamadas feitas na mesma volta da conclusão foram executadas e registradas.
            return ResultadoEtapa2(conclusao, ponte.chamadas, volta)
        messages.append({"role": "user", "content": resultados})
    raise LimiteVoltas(f"Sem conclusão após {max_voltas} voltas de ferramentas.")
