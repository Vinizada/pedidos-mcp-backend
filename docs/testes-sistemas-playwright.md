# Testes de Sistema — Playwright

## 1. Objetivo

Este documento apresenta a estratégia, implementação e resultados dos testes automatizados de sistema realizados no projeto **Pedidos MCP Backend**, utilizando Playwright.

O objetivo é validar o comportamento da aplicação por meio da interface web, verificando a navegação entre os módulos, a consulta de informações e a consistência dos dados apresentados ao usuário.

A automação busca aumentar a confiabilidade do sistema, reduzir a necessidade de validações manuais repetitivas e fornecer evidências da execução dos cenários de teste.

## 2. Escopo dos testes

Nesta primeira etapa, foram contemplados quatro módulos principais da aplicação:

- Produtos;
- Clientes;
- Documentos;
- Pedidos.

Os testes foram desenvolvidos utilizando Playwright com TypeScript, simulando interações reais de um usuário com a aplicação.

A autenticação é realizada por meio da variável de ambiente `API_TOKEN`, evitando a exposição de credenciais diretamente no código-fonte.

## 3. Ambiente e tecnologias utilizadas

| Tecnologia | Finalidade |
|---|---|
| Playwright | Automação de testes de interface |
| TypeScript | Linguagem utilizada na implementação |
| Node.js | Ambiente de execução |
| npm | Gerenciamento de dependências |
| Chromium | Navegador utilizado nos testes |
| VS Code | Ambiente de desenvolvimento |
| Docker | Execução dos serviços da aplicação |

### 3.1 Estrutura dos testes

Os testes automatizados estão organizados em `tests/e2e/`.

```text
tests/
└── e2e/
    ├── produtos.spec.ts
    ├── clientes.spec.ts
    ├── documentos.spec.ts
    └── pedidos.spec.ts
```

Arquivo de configuração:

- `playwright.config.ts`: configurações gerais de execução, navegador, evidências, screenshots e gravação de vídeos.
- `tsconfig.json`: configurações de compilação e análise estática do TypeScript.

## 4. Estratégia de testes

Foi adotada a abordagem de testes de sistema, utilizando a interface web da aplicação para executar operações e validar os resultados apresentados.

Os cenários contemplam:

- Acesso e autenticação na aplicação;
- Navegação entre os módulos;
- Consulta de registros;
- Validação de informações apresentadas;
- Verificação dos dados de pedidos originados do processamento de documentos pela IA.

Os testes utilizam seletores baseados em roles, textos e elementos acessíveis da interface, buscando maior legibilidade e manutenção da automação.

## 5. Casos de teste implementados

### 5.1 PW-001 — Consulta de produtos

**Módulo:** Produtos

**Objetivo:** Validar a consulta de produtos por meio do código EAN.

**Procedimento:**

1. Acessar a aplicação.
2. Realizar autenticação.
3. Acessar o módulo Produtos.
4. Informar o EAN `2990000000019`.
5. Executar a pesquisa.
6. Validar as informações retornadas.

**Resultados esperados:**

- Produto localizado corretamente.
- Descrição: Café moído tradicional 500 g.
- EAN: `2990000000019`.
- SKU: `MER-001`.
- Preço: R$ 18,90.

**Resultado obtido:** Aprovado.

### 5.2 PW-002 — Consulta de clientes

**Módulo:** Clientes

**Objetivo:** Validar a consulta de clientes por meio de seu código de identificação.

**Procedimento:**

1. Acessar a aplicação.
2. Realizar autenticação.
3. Acessar o módulo Clientes.
4. Pesquisar pelo código `CLI-001`.
5. Validar os dados apresentados.

**Resultados esperados:**

- Cliente localizado corretamente.
- Nome: Mercado Demonstrativo Alfa.
- Código: `CLI-001`.
- E-mail: `cliente001@example.invalid`.

**Resultado obtido:** Aprovado.

### 5.3 PW-003 — Consulta de documento TXT processado e validação do pedido

**Módulo:** Documentos

**Objetivo:** Validar a consulta de um documento TXT previamente enviado e processado pelo agente de inteligência artificial, verificando o resultado do processamento e o pedido associado.

**Procedimento:**

1. Acessar a aplicação.
2. Realizar autenticação.
3. Acessar o módulo Documentos.
4. Atualizar a listagem de documentos.
5. Localizar o documento `pedido-002.txt`.
6. Abrir os detalhes do documento.
7. Verificar o resultado do processamento.
8. Acessar o pedido associado.
9. Validar cliente, produtos e valor total.

**Resultados esperados:**

- Documento localizado.
- Processamento concluído.
- Resultado apresentado como Rascunho criado.
- Cliente: `CLI-002`.
- Produto 1: Arroz branco tipo 1 5 kg.
- Produto 2: Feijão preto 1 kg.
- Valor total: R$ 162,60.

**Resultado obtido:** Aprovado.

**Observação:** Este cenário valida a consulta de um documento já processado. Não realiza um novo envio de arquivo nem executa novamente o processamento pela IA.

### 5.4 PW-004 — Consulta e validação de pedidos

**Módulo:** Pedidos

**Objetivo:** Validar a consulta direta de pedidos por meio do módulo específico da aplicação.

**Procedimento:**

1. Acessar a aplicação.
2. Realizar autenticação.
3. Acessar o módulo Pedidos.
4. Localizar o pedido identificado pelo código `3e20d06c`.
5. Abrir os detalhes do pedido.
6. Validar a identificação do cliente.
7. Verificar os produtos apresentados.

**Resultados esperados:**

- Pedido localizado corretamente.
- Detalhes apresentados na interface.
- Identificação do cliente disponível.
- Produto Arroz branco tipo 1 5 kg presente.
- Produto Feijão preto 1 kg presente.

**Resultado obtido:** Aprovado.

## 6. Resultados consolidados

Os quatro cenários foram executados individualmente e, posteriormente, em conjunto por meio da suíte Playwright.

Todos