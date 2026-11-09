"""Pendências estruturadas de um esclarecimento, para uma pessoa resolver na interface.

Função pura: usa a decisão final dos guardrails, os itens verificados e o que as ferramentas
devolveram. Nada aqui decide; só organiza as opções que a pessoa vai confirmar.
"""

from typing import Any

from extrator.guardrails_extracao import ItemVerificado
from extrator.guardrails_resolucao import RegistroChamada, ResultadoResolucao

MAX_OPCOES = 5
CAMPOS_PRODUTO = ("produto_id", "sku", "ean", "nome", "marca", "embalagem", "preco_unitario")


def opcao_produto(candidato: dict[str, Any]) -> dict[str, Any]:
    opcao = {campo: candidato.get(campo) for campo in CAMPOS_PRODUTO}
    opcao["produto_id"] = str(opcao["produto_id"])
    opcao["estoque_disponivel"] = int(candidato.get("estoque_disponivel") or 0)
    return opcao


def _candidatos(chamadas: list[RegistroChamada]) -> dict[int, dict[str, dict]]:
    por_item: dict[int, dict[str, dict]] = {}
    for chamada in chamadas:
        if chamada.ferramenta == "buscar_produtos" and isinstance(chamada.alvo, int):
            for candidato in (chamada.resultado or {}).get("candidatos", []):
                por_item.setdefault(chamada.alvo, {})[str(candidato["produto_id"])] = candidato
    return por_item


def _clientes(chamadas: list[RegistroChamada]) -> dict[str, dict]:
    vistos: dict[str, dict] = {}
    for chamada in chamadas:
        if chamada.ferramenta == "buscar_cliente":
            for cliente in (chamada.resultado or {}).get("clientes", []):
                vistos[str(cliente["cliente_id"])] = cliente
    return vistos


def montar_pendencias(
    resultado: ResultadoResolucao,
    itens: list[ItemVerificado],
    cliente_codigo: str | None,
    cliente_nome: str | None,
    chamadas: list[RegistroChamada],
) -> dict[str, Any] | None:
    """Pendências no formato de `Pendencias` do backend, ou None fora de esclarecimento."""
    if resultado.decisao != "pedir_esclarecimento":
        return None
    candidatos = _candidatos(chamadas)
    verificados = {item.idx: item for item in itens}
    pendentes = []
    for final in resultado.itens:
        item = verificados[final.idx]
        do_item = candidatos.get(final.idx, {})
        base = {
            "item_idx": final.idx,
            "descricao": item.descricao,
            "quantidade_texto": item.quantidade_texto,
            "unidade_texto": item.unidade_texto,
        }
        if final.decisao == "criar_rascunho" and final.produto_id:
            escolhido = do_item.get(final.produto_id)
            pendentes.append(
                base
                | {
                    "situacao": "resolvido",
                    "produto_id": final.produto_id,
                    "quantidade": final.quantidade,
                    "opcoes": [opcao_produto(escolhido)] if escolhido else [],
                }
            )
            continue
        sugeridos = list(final.opcoes) or [c for c in do_item.values() if c.get("ativo")]
        pendentes.append(
            base
            | {
                "situacao": "pendente",
                "codigo": final.codigo,
                "pergunta": final.mensagem,
                "produto_id": None,
                "quantidade": item.quantidade,
                "opcoes": [opcao_produto(c) for c in sugeridos[:MAX_OPCOES]],
            }
        )

    vistos = _clientes(chamadas)
    ativos = [
        {"cliente_id": str(c["cliente_id"]), "codigo": c["codigo"], "nome": c["nome"]}
        for c in vistos.values()
        if c.get("ativo")
    ][:20]
    cliente = {
        "codigo_documento": cliente_codigo,
        "nome_documento": cliente_nome,
        "opcoes": ativos,
    }
    if resultado.cliente_id:
        cliente |= {"situacao": "resolvido", "cliente_id": resultado.cliente_id, "pergunta": None}
    else:
        cliente |= {
            "situacao": "pendente",
            "cliente_id": None,
            "pergunta": resultado.cliente_pergunta,
        }
    return {"cliente": cliente, "itens": pendentes}
