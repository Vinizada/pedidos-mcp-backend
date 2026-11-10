# Relatório de QA — Avaliação funcional da solução de pedidos

## 1. Objetivo

Avaliar a capacidade da solução baseada em IA de interpretar documentos de pedidos, identificar clientes e produtos, extrair quantidades e decidir corretamente entre criar um rascunho, solicitar esclarecimentos ou rejeitar o pedido.

A avaliação busca demonstrar, por meio de uma métrica principal e de um conjunto controlado de exemplos, como verificar se a solução funciona de acordo com os resultados esperados.

## 2. Escopo e ambiente

- Data da execução: 10/11/2026.
- Modelo: Claude Sonnet 5.5.
- Versão do prompt: v1.
- Repetições: uma por caso.
- Conjunto: original.
- Total: 20 documentos.
- Formatos: 10 PDFs e 10 arquivos TXT.
- Catálogo: 100 produtos.
- Base de clientes: 15 clientes.

Os documentos e gabaritos são fictícios e foram fornecidos pelo próprio projeto. Os gabaritos foram utilizados exclusivamente pelo avaliador para comparação dos resultados, não sendo enviados ao agente.

## 3. Métrica principal

### Exact Cart Accuracy (acerto exato do carrinho)

É a proporção de documentos em que a solução reproduz integralmente o resultado esperado.

Para considerar um caso correto:
- A decisão deve corresponder ao gabarito.
- Quando a decisão for criar rascunho, o cliente deve corresponder ao esperado.
- Quando a decisão for criar rascunho, o conjunto de produtos identificados (EAN) e suas respectivas quantidades deve corresponder exatamente ao gabarito.
- Para esclarecimentos e rejeições, a decisão deve corresponder ao resultado esperado.

Fórmula:

`Exact Cart Accuracy = (documentos com resultado integralmente correto / total de documentos avaliados) × 100`

Meta sugerida para futuras execuções: pelo menos 95%. Essa meta é um critério de acompanhamento proposto para o projeto, não um requisito originalmente definido pelo manifesto.

## 4. Conjunto de avaliação

O conjunto contém 20 documentos, distribuídos em três categorias de decisão:

| Categoria | Quantidade | Percentual |
|---|---:|---:|
| Criar rascunho | 10 | 50% |
| Pedir esclarecimento | 5 | 25% |
| Rejeitar | 5 | 25% |
| **Total** | **20** | **100%** |

### 4.1 Matriz dos casos

| Caso | Cenário | Esperado | Obtido | Carrinho | Revisão |
|---|---|---|---|---|---|
| pedido-001 | Pedido por EAN | criar_rascunho | criar_rascunho | OK | Não |
| pedido-002 | Reposição por código interno | criar_rascunho | criar_rascunho | OK | Não |
| pedido-003 | Solicitação em linguagem natural | criar_rascunho | criar_rascunho | OK | Sim |
| pedido-004 | Lista abreviada | criar_rascunho | criar_rascunho | OK | Sim |
| pedido-005 | Caixa fechada com código explícito | criar_rascunho | criar_rascunho | OK | Não |
| pedido-006 | Embalagens distintas | criar_rascunho | criar_rascunho | OK | Sim |
| pedido-007 | Sinônimos no pedido | criar_rascunho | criar_rascunho | OK | Sim |
| pedido-008 | Orçamento com observações | criar_rascunho | criar_rascunho | OK | Não |
| pedido-009 | Itens com descrições completas | criar_rascunho | criar_rascunho | OK | Sim |
| pedido-010 | Itens numerados | criar_rascunho | criar_rascunho | OK | Não |
| pedido-011 | Marca não especificada | pedir_esclarecimento | pedir_esclarecimento | OK | Não |
| pedido-012 | Código inexistente no catálogo | rejeitar | rejeitar | OK | Não |
| pedido-013 | Produto sem estoque | rejeitar | rejeitar | OK | Não |
| pedido-014 | Produto inativo | rejeitar | rejeitar | OK | Não |
| pedido-015 | Quantidade zero | rejeitar | rejeitar | OK | Não |
| pedido-016 | Quantidade negativa | rejeitar | rejeitar | OK | Não |
| pedido-017 | Fração de embalagem indivisível | pedir_esclarecimento | pedir_esclarecimento | OK | Não |
| pedido-018 | Caixa sem definição de embalagem | pedir_esclarecimento | pedir_esclarecimento | OK | Não |
| pedido-019 | Cliente não cadastrado | pedir_esclarecimento | pedir_esclarecimento | OK | Não |
| pedido-020 | Quantidade ausente | pedir_esclarecimento | pedir_esclarecimento | OK | Não |

## 5. Resultados

| Indicador | Resultado |
|---|---:|
| Documentos avaliados | 20 |
| Exact Cart Accuracy | 100.0% |
| Acerto da decisão | 100.0% |
| F1 dos itens | 1.000 |
| EANs alucinados | 0 |
| Pedidos indevidos | 0 |
| Taxa de revisão | 25.0% |
| Falhas de processamento | 0 |
| Custo total | US$ 0.3538 |
| Custo médio por documento | US$ 0.0177 |
| Latência p50 | 8.5 s |
| Latência p95 | 19.3 s |

### 5.1 Matriz de decisão observada

| Resultado esperado → obtido | Quantidade |
|---|---:|
| criar_rascunho->criar_rascunho | 10 |
| pedir_esclarecimento->pedir_esclarecimento | 5 |
| rejeitar->rejeitar | 5 |

### 5.2 Casos encaminhados para revisão humana

Foram encaminhados 5 documentos para revisão (25.0% do conjunto):

- pedido-003: identificação de produto por descrição.
- pedido-004: identificação de produto por descrição.
- pedido-006: identificação de produto por descrição.
- pedido-007: identificação de produto por descrição.
- pedido-009: identificação de produto por descrição.

Os casos encaminhados para revisão tiveram carrinho e decisão corretos na comparação com os gabaritos. A sinalização ocorreu porque a identificação foi realizada por descrição, sem código EAN ou SKU. Esse comportamento representa uma salvaguarda para revisão humana, e não uma divergência funcional observada nesses casos.

## 6. Testes automatizados do projeto

Além da avaliação funcional da IA, a suíte automatizada do projeto foi executada no ambiente de testes.

- Resultado: 430 testes aprovados.
- Falhas: nenhuma.
- Comando utilizado:

```bash
docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider
```

Essa suíte verifica o comportamento implementado no código. Ela é uma evidência complementar e não substitui a avaliação da solução de IA com os documentos e respectivos gabaritos.

## 7. Limitações da avaliação

- Os documentos são sintéticos, não pedidos reais de clientes.
- Os PDFs utilizam um único layout e possuem texto selecionável.
- Não foi avaliada a capacidade de OCR de documentos escaneados nesta execução.
- Foi realizada apenas uma repetição por documento.
- Foi avaliado somente o modelo Claude Sonnet 5.5 nesta execução.
- O resultado de 100% está restrito ao conjunto e às condições avaliadas; não garante o mesmo desempenho em documentos reais ou cenários não cobertos.
- A latência pode variar entre execuções e deve ser acompanhada em avaliações futuras.

## 8. Conclusão

Na execução realizada em 10/11/2026, a solução apresentou 100% de Exact Cart Accuracy nos 20 documentos avaliados. Todas as decisões corresponderam aos gabaritos, e os carrinhos criados reproduziram os produtos e as quantidades esperados.

Não foram observados EANs alucinados, pedidos indevidos ou falhas de processamento. Cinco documentos foram sinalizados para revisão humana por identificação baseada em descrição, sem divergência em relação aos gabaritos.

Os resultados indicam que a solução atendeu aos cenários cobertos pelo conjunto de avaliação. Para aumentar a confiança, recomenda-se ampliar a diversidade dos documentos, incluir pedidos reais anonimizados quando autorizados, testar layouts distintos e documentos escaneados, e executar múltiplas repetições.

## 9. Evidências

- Execução detalhada: `avaliacao/20261110-214443/resultados.jsonl`.
- Resumo estruturado: `avaliacao/20261110-214443/resumo.json`.
- Resumo original do avaliador: `avaliacao/20261110-214443/resumo.md`.
- Manifesto do conjunto: `data/gabaritos/manifesto.json`.
- Gabaritos individuais: `data/gabaritos/pedido-001.json` a `pedido-020.json`.

Os arquivos de evidência devem ser preservados junto ao relatório para permitir rastreabilidade e reprodução da análise.
