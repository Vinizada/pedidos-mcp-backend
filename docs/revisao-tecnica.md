# Revisão técnica

Revisão de 7 de novembro de 2026, cobrindo código Python, banco, contratos, interface e execução local.

## Parecer

A estrutura é adequada para o backend do trabalho e para uso local pelo grupo. A separação entre contratos, serviços e persistência permite conectar o processamento externo sem duplicar regras de preço, estoque e criação de pedidos. Não há necessidade de adicionar microserviços ou um framework de frontend para esse escopo.

A revisão identificou falhas reais na persistência e pontos de acabamento que foram corrigidos. O resultado continua sendo uma aplicação para um grupo confiável, com chave compartilhada; não equivale a um sistema comercial com usuários, autorização individual e confirmação de vendas.

## Correções realizadas

| Área | Problema identificado | Correção |
|---|---|---|
| Persistência | Uma exceção após o COMMIT poderia apagar um anexo já registrado. | O original é preservado quando o resultado do COMMIT é desconhecido; falhas anteriores removem apenas arquivos criados pela operação. |
| Upload | Conferir apenas o início `%PDF-` aceitava arquivos truncados. | Validação estrutural, limite de páginas e rejeição de documentos criptografados. |
| Banco | Faltavam índices de acesso e verificação completa do EAN. | Migrações adicionam índices de FK/ordenação e constraint de checksum EAN-13. |
| Consultas | Ordenação parcialmente incompatível com os índices; risco de consultas repetidas para clientes. | Ordenação consistente e carregamento explícito dos relacionamentos. |
| Operação | A saúde do banco não garantia migrações e armazenamento disponíveis. | Readiness verifica revisão Alembic, vínculo documental e escrita no diretório de uploads. |
| Recursos | O pool poderia deixar de ser descartado no encerramento excepcional. | Cleanup garantido em `finally`; engine e factory reutilizados. |
| Interface | Identificadores técnicos dominavam a leitura dos pedidos. | Nome/código do cliente em destaque; UUIDs e hash em detalhes recolhidos. |
| Sessão | Respostas pendentes poderiam alterar a tela depois de sair. | Cancelamento das requisições e invalidação da sessão anterior. |
| Responsividade | Rótulos de acessibilidade escapavam da tabela e alargavam a página no celular. | Contenção dos rótulos dentro da tabela, preservando sua rolagem horizontal e leitura por tecnologia assistiva. |

## Práticas preservadas

- PKs, FKs, UNIQUE, CHECK e exclusão de intervalos de preço no PostgreSQL.
- Preços em `Decimal`/`NUMERIC`, calculados no servidor e preservados nos itens.
- Cabeçalho e itens na mesma transação; idempotência protegida inclusive sob concorrência.
- SQL parametrizado e consultas MCP delimitadas, sem execução de SQL arbitrário.
- Autenticação antes da leitura do corpo, limites de requisição e arquivos físicos identificados por UUID.
- Chave somente na memória da interface, conteúdo renderizado com `textContent` e recursos locais.
- Migrações versionadas, dependências fixadas e testes com PostgreSQL real separado do banco de uso.

## Verificação e limites

Os 132 testes passaram dentro do Docker, cobrindo contratos, integridade, upload, falhas de persistência, autenticação, preços, paginação, transações e concorrência. Ruff e a verificação de sintaxe JavaScript passaram. A demonstração MCP confirmou consulta, leitura de anexo, criação vinculada, repetição idempotente e estoque preservado. O fluxo visual foi conferido no Chrome, sem erros no console; a largura da página foi verificada entre 320 e 1440 pixels.

O procedimento Docker foi executado no macOS. As instruções PowerShell estão documentadas, mas não foram executadas em uma máquina Windows nesta revisão. Há um aviso de depreciação no adaptador de testes Starlette/httpx; ele não representa falha da suíte nem impede a aplicação de rodar.

O upload não inicia interpretação automática. OCR, escolha e avaliação do modelo e orquestração do consumidor pertencem à integração do grupo. A busca recupera candidatos; o consumidor deve tratar ambiguidades antes de criar o rascunho.

Não há reserva de estoque, confirmação comercial, fila de processamento, antivírus ou isolamento por usuário. Banco e arquivos precisam de backup conjunto. Uma falha abrupta pode deixar arquivos órfãos; a conciliação é manual para evitar exclusão de originais confirmados. Esses limites estão descritos em [desenvolvimento](desenvolvimento.md).
