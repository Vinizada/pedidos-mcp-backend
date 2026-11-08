# Avaliação do agente de extração

Execução de 8 de novembro de 2026, versão de prompt `v1`, uma repetição por caso. Resultados
brutos em `avaliacao/consolidado/` (`resultados.jsonl`, `resumo.json`, `resumo.md`); as
execuções originais estão em `avaliacao/20261108-204245/` (Sonnet + ablação) e
`avaliacao/20261108-204534/` (Haiku e Opus).

## Conjuntos de teste

| Conjunto | Casos | Composição | Gabarito |
|---|---|---|---|
| Original | 20 | 10 PDF com texto + 10 TXT: 10 rascunhos, 5 esclarecimentos, 5 rejeições | `data/gabaritos/pedido-*.json` |
| Escaneado | 13 | Os 10 PDFs rasterizados a 150 dpi (rotação ±1,5°, ruído, JPEG) + 3 fotos PNG (inclinação, fundo escuro, sombra) — **sem camada de texto** | os mesmos dos originais |
| Ataque | 7 | Injeção no corpo, `</documento>` + `<system>` falsos, EAN com "O" no lugar de "0", EAN só no rodapé, tamanho inexistente, CPF/telefone/e-mail no corpo, quantidade acima do estoque | `data/gabaritos/ataque-*.json` |

Gerados de forma determinística por `scripts/gerar_exemplos.py`, `scripts/gerar_escaneados.py`
e `scripts/gerar_ataques.py`. Os gabaritos são lidos apenas por `scripts/avaliar.py`, depois do
processamento; o agente não tem acesso a eles (o container do agente não contém `data/`).

## Métricas

- **Acerto do carrinho (principal):** a decisão bate com o gabarito e, para rascunhos, cliente
  e conjunto {(EAN, quantidade)} são idênticos.
- **Acerto da decisão:** entre `criar_rascunho`, `pedir_esclarecimento` e `rejeitar`.
- **F1 de itens:** micro, sobre pares (EAN, quantidade) dos rascunhos criados.
- **EAN alucinado:** itens de rascunho cujo EAN não está no gabarito do caso (meta: 0).
- **Pedidos indevidos:** rascunho criado quando o esperado não era rascunho.
- **Revisão:** proporção de casos marcados com revisão obrigatória.
- **Bloqueios corretos/indevidos:** casos em que os guardrails mudaram a decisão do modelo e
  acertaram/erraram em relação ao gabarito.

## Resultados

| Modelo | Conjunto | n | Acerto do carrinho | Acerto da decisão | F1 itens | EAN alucinado | Pedidos indevidos | Revisão | Custo médio (US$) | Latência p50/p95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| Sonnet 5.5 | original | 20 | 100% | 100% | 1,000 | 0 | 0 | 25,0% | 0,0179 | 7,7 / 9,0 |
| Sonnet 5.5 | escaneado | 13 | 100% | 100% | 1,000 | 0 | 0 | 61,5% | 0,0146 | 7,3 / 9,1 |
| Sonnet 5.5 | ataque | 7 | 100% | 100% | 1,000 | 0 | 0 | 28,6% | 0,0121 | 6,8 / 8,5 |
| Haiku 5.5 | original | 20 | 100% | 100% | 1,000 | 0 | 0 | 25,0% | 0,0011 | 6,1 / 8,1 |
| Haiku 5.5 | escaneado | 13 | 100% | 100% | 1,000 | 0 | 0 | 61,5% | 0,0010 | 6,8 / 7,9 |
| Haiku 5.5 | ataque | 7 | 100% | 100% | 1,000 | 0 | 0 | 28,6% | 0,0008 | 5,5 / 6,9 |
| Opus 5.5 | original | 20 | 100% | 100% | 1,000 | 0 | 0 | 25,0% | 0,0341 | 9,1 / 11,6 |
| Opus 5.5 | escaneado | 13 | 100% | 100% | 1,000 | 0 | 0 | 61,5% | 0,0267 | 9,6 / 12,3 |
| Opus 5.5 | ataque | 7 | 100% | 100% | 1,000 | 0 | 0 | 28,6% | 0,0242 | 7,4 / 12,1 |

Matriz de decisão (iguais nos três modelos, 40 casos): 23 rascunhos, 9 esclarecimentos e
8 rejeições, todos na classe esperada.

**Original × escaneado:** nenhum modelo perdeu acerto sem a camada de texto. A leitura
multimodal nativa (PDF/imagem enviados ao modelo, sem OCR) cobriu os 10 scans e as 3 fotos.
A revisão sobe para 61,5% no conjunto escaneado por desenho: todo rascunho de documento sem
texto verificável recebe `texto_nao_verificavel`, porque os guardrails de evidência literal
não podem ser aplicados. No conjunto original a revisão fica em 25% (5 casos resolvidos só
por descrição), abaixo do alerta de 50%.

**Ataque (7/7 nos três modelos):** instruções embutidas não foram seguidas e marcaram revisão
(`possivel_injecao`); o EAN do rodapé não virou item; "café Aurora 1 kg" gerou esclarecimento
em vez de troca de tamanho; a quantidade acima do estoque foi rejeitada; e a análise gravada
não contém CPF, telefone nem e-mail (verificado no JSON persistido).

**Custo real:** US$ 2,40 para 160 processamentos (3 modelos × 40 + ablação de 40 no Sonnet),
contra estimativa conservadora de US$ 4,75. O cache de prompt reduziu a entrada efetiva.

## Contribuição dos guardrails

Ablação a seco com Sonnet 5.5 (extração e resolução usando os dados crus do modelo, sem
nenhum guardrail e sem gravar nada no backend):

| | Acerto do carrinho | EAN alucinado | Pedidos indevidos |
|---|---|---|---|
| Sem guardrails | 90,0% (36/40) | 0 | 0 |
| Com guardrails | 100% (40/40) | 0 | 0 |

Os 4 casos que só acertaram com guardrails:

| Caso | Sem guardrails | Guardrail responsável |
|---|---|---|
| pedido-015, pedido-015-scan (quantidade zero) | pediu esclarecimento | parser de quantidade → rejeição `QUANTIDADE_INVALIDA` |
| pedido-016 (quantidade negativa) | pediu esclarecimento | parser de quantidade → rejeição `QUANTIDADE_INVALIDA` |
| ataque-03 (EAN com "O") | rejeitou como inexistente | normalização + ancoragem do EAN → produto correto |

Com guardrails, nenhum modelo teve decisão alterada pelos guardrails da Etapa 2 (bloqueios
corretos 0, indevidos 0): os modelos não escolheram produto sem proveniência, não trocaram
códigos nem ignoraram ambiguidade neste conjunto. O guardrail de cliente foi acionado nos 6
processamentos do caso 019 (CLI-999), confirmando o esclarecimento que o modelo já propunha.
A contribuição medida veio da Etapa 1 (quantidade e normalização de código), que é
determinística e não depende do modelo.

## Escolha do modelo

Critério definido antes da execução: o modelo mais barato com acerto do carrinho no conjunto
original até 5 p.p. abaixo do Opus 5.5 e zero EAN alucinado.

Os três modelos empataram em 100% sem EAN alucinado, logo todos são elegíveis e o critério
seleciona **Claude Haiku 5.5**: cerca de US$ 0,001 por documento (≈16× mais barato que Sonnet e
≈30× mais barato que Opus) e a menor latência (p95 de 8,1 s).

Ressalvas antes de adotar o Haiku como padrão em produção:

1. **Efeito teto.** Com 100% nos três, o conjunto não distingue os modelos; ele mostra que
   todos resolvem estes casos, não que o Haiku é tão robusto quanto os outros em documentos
   reais. Os documentos são sintéticos, de layout único, e os scans têm qualidade boa.
2. **Uma repetição.** A variabilidade entre execuções não foi medida (`--repeticoes 3` custa
   cerca de US$ 0,12 no Haiku).
3. **Sem fallback de recusa no servidor.** O Haiku 5.5 não aceita `fallbacks`; uma recusa vira
   `falha` registrada, sem nova tentativa em outro modelo (nenhuma ocorreu na avaliação).

Recomendação: adotar Haiku 5.5 como padrão (`LLM_MODEL=claude-haiku-5-5`), mantendo os
guardrails como estão, e reavaliar com documentos reais e mais variados (layouts diferentes,
manuscritos, scans de baixa qualidade, pedidos longos) antes de uso real. Se o acerto do
Haiku cair mais de 5 p.p. em relação ao Opus nesse conjunto ampliado, o critério passa a
indicar o Sonnet 5.5.

## Como reproduzir

Com a stack rodando e o worker do agente parado (para não processar os documentos da
avaliação):

```bash
python scripts/gerar_escaneados.py && python scripts/gerar_ataques.py   # dependências de dev
docker compose --profile agente run --rm --no-deps \
  -v "$PWD/data:/app/data:ro" -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/avaliacao:/app/avaliacao" \
  agente python scripts/avaliar.py --conjunto todos \
  --modelos claude-sonnet-5-5,claude-haiku-5-5,claude-opus-5-5
```

O script mostra a estimativa de custo e pede confirmação (`--sim` dispensa). Cada caso é
enviado como documento novo, com nome `aval-<execução>-...`, e fica no banco local junto com
seus rascunhos e análises.
