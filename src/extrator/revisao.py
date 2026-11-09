"""Revisão humana proporcional ao risco: marca rascunhos que exigem conferência prioritária.

A marcação não impede a criação do rascunho (que não reserva nem baixa estoque); ela só
ordena a atenção de quem confere.
"""

from decimal import Decimal

from extrator.guardrails_resolucao import ResultadoResolucao

ORDEM = (
    "texto_nao_verificavel",
    "casamento_por_descricao",
    "possivel_injecao",
    "extracao_suspeita",
    "total_acima_limite",
    "quantidade_acima_limite",
    "decisao_rebaixada",
)


def motivos_revisao(
    resolucao: ResultadoResolucao,
    motivos_extracao: list[str],
    total: Decimal | None,
    limite_total: Decimal,
    limite_quantidade: int,
) -> list[str]:
    if resolucao.decisao != "criar_rascunho":
        return []
    motivos = set(motivos_extracao)
    if resolucao.casamento_por_descricao:
        motivos.add("casamento_por_descricao")
    if total is not None and total > limite_total:
        motivos.add("total_acima_limite")
    if any(item["quantidade"] > limite_quantidade for item in resolucao.carrinho):
        motivos.add("quantidade_acima_limite")
    if resolucao.rebaixada:
        motivos.add("decisao_rebaixada")
    return [m for m in ORDEM if m in motivos] + sorted(motivos - set(ORDEM))
