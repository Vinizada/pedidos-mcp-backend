# Riscos, mitigações e LGPD

Cada risco abaixo lista a mitigação implementada e a evidência: testes automatizados (com
modelo simulado, sem rede) e resultados da avaliação real descrita em
[avaliacao.md](avaliacao.md).

## 1. Alucinação (EAN, produto, quantidade ou cliente inexistentes)

**Risco:** o modelo inventa ou "conserta" um código, troca um produto por outro parecido,
arredonda uma quantidade ou escolhe um cliente que não está no documento.

**Mitigação:**

- O modelo só **lê** o catálogo. Rascunhos e análises são gravados pelo código, depois das
  verificações (lista fechada de ferramentas na ponte MCP).
- Cada item precisa de **evidência literal** no documento; códigos precisam aparecer no
  documento e, no caso de EAN, ter checksum válido.
- A quantidade é calculada por um **parser determinístico**. O número que o modelo devolve é
  ignorado.
- Todo `produto_id` precisa ter vindo de uma busca feita **para aquele item** nesta execução.
- Código citado sem produto vendável só admite rejeição: **nunca há substituição**.
- Itens sem código passam por conferência de medida, contagem e marca e por detecção de
  ambiguidade.
- O backend revalida preço, estoque, cliente e unidade antes de criar o rascunho.

**Evidência:**

- Testes de tabela para cada guardrail (catálogo abaixo).
- Avaliação: **0 EAN alucinado e 0 pedidos indevidos** em 120 processamentos (3 modelos × 40
  casos).
- Sem guardrails, o acerto do carrinho cai de 100% para 90%: zero e negativo viraram
  esclarecimento em vez de rejeição, e o EAN adulterado ("O" no lugar de "0") não foi
  resolvido.

## 2. Vazamento de dados pessoais (LGPD)

**Risco:** documentos de clientes contêm nomes, CPF/CNPJ, telefones e e-mails. Esses dados
podem acabar em logs, no banco ou no provedor do modelo sem necessidade.

**Mitigação:**

- **Minimização no envio:** o documento vai ao modelo uma única vez (Etapa 1). A Etapa 2 recebe
  só os itens extraídos. Para TXT, `AGENTE_MASCARAR_PII_TXT=true` mascara CPF, CNPJ, telefone e
  e-mail antes do envio. Não há como mascarar PDF ou imagem.
- **Minimização no armazenamento:** perguntas, motivos e todas as strings da extração são
  gravados com CPF, CNPJ, telefone e e-mail mascarados (`pedidos/privacidade.py`). A análise
  guarda só os itens extraídos, nunca o texto integral do documento.
- **Logs:** apenas IDs, SHA-256, decisão, modelo, tokens, custo e latência. Mensagens de erro
  da API e de validação não são logadas, porque podem citar trechos enviados.
- **Isolamento:** o container do agente não tem credenciais do banco nem acesso aos volumes. O
  token do grupo vai apenas ao endpoint configurado.
- **Dados de teste:** todos os clientes, produtos e documentos são sintéticos.

**Evidência:**

- `test_logs_have_metrics_but_no_document_content`.
- `test_pii_is_masked`, `test_pii_is_masked_before_storage`,
  `test_pii_masking_reaches_nested_json_strings_only`,
  `test_business_identifiers_are_not_masked`.
- `test_record_contains_items_but_not_full_document`.
- Caso `ataque-06`: CPF, telefone e e-mail ausentes do JSON persistido nos três modelos.

### Bases e cuidados LGPD para uso com dados reais

| Tema | Orientação |
|---|---|
| Finalidade e base legal | Processar pedidos de compra de clientes empresariais: execução de contrato ou procedimentos preliminares (art. 7º, V) e, para dados de contato dos compradores, legítimo interesse com avaliação documentada (art. 7º, IX) |
| Necessidade | Só o necessário para montar o carrinho segue adiante; contatos pessoais não são usados na decisão e são mascarados no armazenamento |
| Operador e transferência internacional | O provedor do modelo atua como operador e processa fora do Brasil: exige contrato com cláusulas de proteção (art. 33) e registro no inventário de dados |
| Retenção no provedor | Siga a política de retenção do contrato vigente com o provedor; para dados reais, prefira retenção mínima ou zero (ZDR) quando contratável |
| Retenção local | Originais e análises ficam no volume e no banco até remoção manual. **Não há expiração automática**: a organização deve definir o prazo e executar a remoção (pedidos têm FK `RESTRICT` para preservar histórico) |
| Direitos do titular | Acesso e eliminação são atendidos manualmente a partir do `documento_id`; o mascaramento reduz o que precisa ser eliminado |
| Alternativa de privacidade estrita | Modelo local via Ollama (mais OCR para scans), com perda esperada de acurácia; não implementado |

## 3. Custo de processamento

**Risco:** documentos longos, loops de ferramentas ou volume inesperado elevam o gasto com a API.

**Mitigação:**

- Limites antes de chamar o modelo: até 10 páginas por PDF, 50 mil caracteres por TXT e 5 MB
  por imagem. Acima disso, o documento vira esclarecimento.
- **Teto por documento** em tokens e em US$, verificado a cada chamada.
- **Limite de voltas** do loop de ferramentas.
- **Orçamento diário** no worker e **disjuntor** para falhas seguidas.
- **Cache de prompt** em instruções e ferramentas.
- Etapa 2 pulada quando todos os itens já foram decididos pelos guardrails.
- Modelo mais barato que atendeu ao critério de qualidade: Haiku 5.5, cerca de US$ 0,001 por
  documento.

**Evidência:**

- `test_counter_enforces_document_ceilings_and_prices_fallback_model`,
  `test_document_cost_ceiling_interrupts_loop`, `test_document_cost_ceiling_registers_failure`.
- `test_loop_stops_at_turn_limit`, `test_daily_budget_stops_processing`,
  `test_circuit_breaker_opens_backs_off_and_resets`.
- `test_fractional_quantity_asks_clarification_without_stage2`.
- Custo real da avaliação: US$ 2,40 para 160 processamentos.

## 4. Injeção de prompt

**Risco:** o documento contém instruções ("ignore as regras", "confirme com desconto", tags que
imitam o sistema) que levam o modelo a agir fora das regras.

**Mitigação:**

- O documento é tratado como dado não confiável, dentro de delimitadores. Tags como
  `</documento>`, `<system>` e `<tool_result>` são neutralizadas em TXT.
- O modelo copia instruções suspeitas em `sinais_de_instrucao`, o que marca revisão
  obrigatória (`possivel_injecao`).
- O modelo não tem ferramentas de escrita, desconto ou confirmação. Mesmo obedecendo, só
  conseguiria propor um rascunho, que passa pelos mesmos guardrails.
- A Etapa 2 não recebe o documento original.

**Evidência:**

- `test_txt_delimiters_inside_document_are_neutralized`, `test_protected_tags_are_neutralized`.
- `test_embedded_instruction_creates_draft_flagged_for_review`.
- `test_write_tools_never_reach_the_model_even_if_listed`,
  `test_model_requested_call_outside_allowlist_is_refused_before_sending`.
- Casos `ataque-01` e `ataque-02`: rascunhos corretos, sem desconto nem confirmação, marcados
  para revisão nos três modelos.

**Limite:** instruções dentro de PDF ou imagem não podem ser neutralizadas antes do modelo. A
defesa nesse caso é estrutural: sem ferramentas de escrita, mais guardrails e revisão.

## Catálogo de guardrails

O identificador é o que aparece em `guardrails_acionados` nas análises. "Decisão" é o efeito
sobre o item ou o documento.

### Entrada (antes do modelo)

| Guardrail | Regra | Decisão | Testes |
|---|---|---|---|
| Integridade do original | SHA-256 do arquivo confere com o upload | `falha` (`DOCUMENTO_CORROMPIDO`) | `test_document_with_divergent_hash_is_refused` |
| Limites de documento | ≤ 10 páginas, ≤ 50 mil caracteres, imagem ≤ 5 MB, formato suportado | esclarecimento sem chamar o modelo | `test_pdf_above_page_limit_is_refused_without_model`, `test_txt_above_character_limit_is_refused`, `test_image_above_api_limit_is_refused` |
| Neutralização de delimitadores | Tags de documento/sistema no TXT tornam-se inertes | — | `test_txt_delimiters_inside_document_are_neutralized` |
| Texto verificável | Página sem texto torna o documento não verificável | revisão `texto_nao_verificavel` | `test_pdf_with_one_scanned_page_is_not_verifiable` |

### Etapa 1 — extração (`guardrails_extracao.py`)

| Guardrail | Regra | Decisão | Testes | Casos |
|---|---|---|---|---|
| `evidencia_literal` | Trecho de evidência existe no documento normalizado | item descartado + revisão `extracao_suspeita` | `test_invented_evidence_discards_item_and_flags_review` | — |
| `ean_ancorado` / `sku_ancorado` | Código na evidência e no documento; EAN com checksum | código anulado; sem descrição útil → esclarecimento `CODIGO_NAO_ANCORADO` | `test_ean_absent_from_document_is_nullified_and_needs_clarification`, `test_ean_with_letter_o_is_anchored_to_digits`, `test_sku_not_in_document_is_nullified` | 001, 008, 010, ataque-03 |
| `quantidade_parser` | Quantidade lida do texto: fração, ausente ou ambígua → esclarecimento; zero ou negativa → rejeição | `QUANTIDADE_FRACIONARIA`, `QUANTIDADE_AUSENTE`, `QUANTIDADE_AMBIGUA`, `QUANTIDADE_INVALIDA` | `test_quantity_parser`, `test_non_integer_quantity_blocks_item`, `test_model_quantity_is_ignored_when_text_says_fraction` | 015, 016, 017, 020 |
| `sanidade_extracao` | Extração vazia com números no texto, ou mais itens que linhas numéricas | revisão `extracao_suspeita` | `test_empty_extraction_of_document_with_numbers_is_suspicious`, `test_more_items_than_numeric_lines_is_suspicious` | — |
| `sinais_de_instrucao` | Instruções embutidas detectadas | revisão `possivel_injecao` | `test_embedded_instructions_flag_review_without_blocking_items` | ataque-01, ataque-02 |

### Etapa 2 — resolução (`guardrails_resolucao.py`)

| Guardrail | Regra | Decisão | Testes | Casos |
|---|---|---|---|---|
| `proveniencia_item` | `produto_id` veio de busca com o `item_idx` do item | esclarecimento `PRODUTO_SEM_PROVENIENCIA` | `test_fabricated_product_id_is_blocked`, `test_product_from_another_items_search_is_blocked`, `test_fabricated_product_never_reaches_an_order` | — |
| `codigo_exato` | Produto escolhido tem exatamente o EAN/SKU citado | esclarecimento `PRODUTO_DIVERGENTE` | `test_product_with_different_code_is_blocked` | — |
| `proibido_substituir` | Busca exata vazia ou produto inativo, sem preço ou sem estoque | rejeição com o código do backend | `test_substitution_for_nonexistent_code_becomes_rejection`, `test_unsellable_coded_product_only_allows_rejection`, `test_inactive_product_chosen_by_model_is_rejected` | 012, 013, 014, ataque-07 |
| `atributos_conferidos` | Medida, contagem e marca citadas aparecem no produto | esclarecimento `ATRIBUTOS_DIVERGENTES` | `test_wrong_size_is_blocked`, `test_evaluation_descriptions_resolve_uniquely_even_against_whole_catalog` | 003, 004, 006, 007, 009, ataque-05 |
| `ambiguidade` | Empate entre candidatos compatíveis, ou "caixa" sem contagem com variantes avulsa e fechada | esclarecimento `AMBIGUIDADE` / `AMBIGUIDADE_EMBALAGEM` | `test_two_compatible_brands_require_clarification_whatever_the_model_chose`, `test_box_without_package_definition_requires_clarification` | 011, 018 |
| `cliente` | Código exato e ativo, ou nome com um único candidato ativo, vindo de `buscar_cliente` | esclarecimento | `test_unknown_client_code_requires_clarification`, `test_homonymous_clients_by_name_require_clarification`, `test_client_must_come_from_client_search` | 019 |
| `item_extra` | Item decidido sem item extraído correspondente, ou duplicado | ignorado + revisão `decisao_rebaixada` | `test_extra_item_is_ignored_and_flags_review` | — |
| `cobertura` | Todo item extraído tem decisão | esclarecimento `ITEM_NAO_RESOLVIDO` | `test_missing_item_decision_requires_clarification` | — |
| `quantidades` | Produtos repetidos consolidados; soma ≤ 100.000 | esclarecimento acima do limite | `test_repeated_product_is_consolidated` | — |
| `coerencia_decisao` | Rascunho sem perguntas e com itens; rejeição com código do backend; esclarecimento com pergunta; nunca promove | ajuste para a decisão coerente mais grave | `test_incoherent_general_decision_is_corrected_upwards`, `test_model_clarification_is_never_promoted_to_draft`, `test_model_rejection_without_backend_code_becomes_clarification` | — |
| `revalidacao_carrinho` | `validar_carrinho` e a criação aceitam o carrinho | rejeição com os problemas do backend | `test_backend_revalidation_problems_become_rejection` | — |

### Ação e registro

| Guardrail | Regra | Testes |
|---|---|---|
| Lista fechada de ferramentas | Só as 4 ferramentas de leitura chegam ao modelo; chamadas fora da lista são recusadas antes de sair | `test_real_backend_tools_become_strict_read_only_definitions`, `test_write_tools_never_reach_the_model_even_if_listed`, `test_write_tool_requested_by_model_is_refused` |
| Vínculo pedido–documento | Análise de rascunho só aceita pedido do mesmo documento (servidor + CHECK no banco) | `test_order_from_another_document_is_rejected`, `test_database_enforces_analysis_coherence` |
| Idempotência | Mesma chave e mesmo conteúdo retornam o mesmo resultado; repetição de documento é pulada | `test_same_key_and_content_returns_same_analysis`, `test_coded_order_becomes_linked_draft_and_is_idempotent` |
| Revisão obrigatória | Rascunho marcado por documento não verificável, casamento por descrição, injeção, extração suspeita, total/quantidade acima do limite ou decisão alterada | `test_review_reasons`, `test_review_filters_documents_and_orders` |
