# Resultado da avaliação — 20261110-214443

Conjuntos: original · repetições: 1 · versão de prompt: v1

| Modelo | Conjunto | n | Acerto do carrinho | Acerto da decisão | F1 itens | EAN alucinado | Pedidos indevidos | Revisão | Falhas | Custo médio (US$) | Latência p50/p95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | original | 20 | 100.0% | 100.0% | 1.000 | 0 | 0 | 25.0% | 0 | 0.0177 | 8.5/19.3 |
| claude-sonnet-5-5 | todos | 20 | 100.0% | 100.0% | 1.000 | 0 | 0 | 25.0% | 0 | 0.0177 | 8.5/19.3 |

## Guardrails

- **claude-sonnet-5-5**: bloqueios corretos 0, indevidos 0, neutros 0; acionamentos {'cliente': 1}.
  - Matriz de decisão (esperado→obtido): {'criar_rascunho->criar_rascunho': 10, 'pedir_esclarecimento->pedir_esclarecimento': 5, 'rejeitar->rejeitar': 5}

## Escolha do modelo

claude-sonnet-5-5 — Mais barato entre os que ficam a até 5 p.p. da referência sem EAN alucinado.
Elegíveis: claude-sonnet-5-5; piso de acerto 95.0%.
