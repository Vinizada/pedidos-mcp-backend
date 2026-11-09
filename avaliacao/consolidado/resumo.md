# Resultado da avaliação — 20261108-204245 + 20261108-204534

Conjuntos: todos · repetições: 1 · versão de prompt: v1

| Modelo | Conjunto | n | Acerto do carrinho | Acerto da decisão | F1 itens | EAN alucinado | Pedidos indevidos | Revisão | Falhas | Custo médio (US$) | Latência p50/p95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | ataque | 7 | 100.0% | 100.0% | 1.000 | 0 | 0 | 28.6% | 0 | 0.0121 | 6.8/8.5 |
| claude-sonnet-5-5 | escaneado | 13 | 100.0% | 100.0% | 1.000 | 0 | 0 | 61.5% | 0 | 0.0146 | 7.3/9.1 |
| claude-sonnet-5-5 | original | 20 | 100.0% | 100.0% | 1.000 | 0 | 0 | 25.0% | 0 | 0.0179 | 7.7/9.0 |
| claude-sonnet-5-5 | todos | 40 | 100.0% | 100.0% | 1.000 | 0 | 0 | 37.5% | 0 | 0.0158 | 7.5/9.0 |
| claude-haiku-5-5 | ataque | 7 | 100.0% | 100.0% | 1.000 | 0 | 0 | 28.6% | 0 | 0.0008 | 5.5/6.9 |
| claude-haiku-5-5 | escaneado | 13 | 100.0% | 100.0% | 1.000 | 0 | 0 | 61.5% | 0 | 0.0010 | 6.8/7.9 |
| claude-haiku-5-5 | original | 20 | 100.0% | 100.0% | 1.000 | 0 | 0 | 25.0% | 0 | 0.0011 | 6.1/8.1 |
| claude-haiku-5-5 | todos | 40 | 100.0% | 100.0% | 1.000 | 0 | 0 | 37.5% | 0 | 0.0010 | 6.1/8.1 |
| claude-opus-5-5 | ataque | 7 | 100.0% | 100.0% | 1.000 | 0 | 0 | 28.6% | 0 | 0.0242 | 7.4/12.3 |
| claude-opus-5-5 | escaneado | 13 | 100.0% | 100.0% | 1.000 | 0 | 0 | 61.5% | 0 | 0.0267 | 9.6/12.3 |
| claude-opus-5-5 | original | 20 | 100.0% | 100.0% | 1.000 | 0 | 0 | 25.0% | 0 | 0.0341 | 9.1/11.6 |
| claude-opus-5-5 | todos | 40 | 100.0% | 100.0% | 1.000 | 0 | 0 | 37.5% | 0 | 0.0299 | 9.1/12.1 |

## Guardrails

- **claude-sonnet-5-5**: bloqueios corretos 0, indevidos 0, neutros 0; acionamentos {'cliente': 2}.
  - Sem guardrails (ablação a seco): acerto do carrinho 90.0% vs 100.0% com guardrails; EAN alucinado 0 vs 0; pedidos indevidos 0 vs 0.
  - Ataque: 7/7 corretos, revisão esperada 7/7, PII mascarada: sim, pedidos indevidos: 0.
  - Matriz de decisão (esperado→obtido): {'criar_rascunho->criar_rascunho': 23, 'pedir_esclarecimento->pedir_esclarecimento': 9, 'rejeitar->rejeitar': 8}
- **claude-haiku-5-5**: bloqueios corretos 0, indevidos 0, neutros 0; acionamentos {'cliente': 2}.
  - Ataque: 7/7 corretos, revisão esperada 7/7, PII mascarada: sim, pedidos indevidos: 0.
  - Matriz de decisão (esperado→obtido): {'criar_rascunho->criar_rascunho': 23, 'pedir_esclarecimento->pedir_esclarecimento': 9, 'rejeitar->rejeitar': 8}
- **claude-opus-5-5**: bloqueios corretos 0, indevidos 0, neutros 0; acionamentos {'cliente': 2}.
  - Ataque: 7/7 corretos, revisão esperada 7/7, PII mascarada: sim, pedidos indevidos: 0.
  - Matriz de decisão (esperado→obtido): {'criar_rascunho->criar_rascunho': 23, 'pedir_esclarecimento->pedir_esclarecimento': 9, 'rejeitar->rejeitar': 8}

## Escolha do modelo

claude-haiku-5-5 — Mais barato entre os que ficam a até 5 p.p. da referência sem EAN alucinado.
Elegíveis: claude-haiku-5-5, claude-opus-5-5, claude-sonnet-5-5; piso de acerto 95.0%.
