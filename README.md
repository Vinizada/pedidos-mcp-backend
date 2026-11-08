# Pedidos

Catálogo de produtos e clientes, recebimento de documentos e criação de rascunhos de pedidos. A aplicação reúne interface web, API REST, servidor MCP e PostgreSQL.

Um agente de IA generativa (pacote `extrator`) lê cada documento enviado — PDF, TXT ou imagem, inclusive escaneados —, extrai cliente e itens, resolve-os contra o catálogo pelas ferramentas MCP e decide entre **criar um rascunho**, **pedir esclarecimento** ou **rejeitar**. Verificações determinísticas (guardrails) conferem cada decisão antes de qualquer gravação. O agente é opcional: sem ele, o backend continua funcionando para consumidores próprios do grupo.

## Recursos

- Produtos e clientes com pesquisa e paginação.
- Upload de PDF, TXT, PNG ou JPEG e consulta dos pedidos e análises vinculados.
- Busca de produtos por EAN/SKU, texto e similaridade semântica.
- Validação de cliente, preço, disponibilidade, unidade e quantidade.
- Agente de extração com Claude (leitura nativa de PDF/imagem, sem OCR), guardrails e revisão humana proporcional ao risco.
- Rascunhos transacionais, com proteção contra repetição concorrente.
- Dados de demonstração: 100 produtos, 15 clientes e 20 documentos com gabaritos.

**Rascunhos não confirmam compras e não reservam ou baixam estoque.**

## 1. Pré-requisitos

Instale [Git](https://git-scm.com/downloads) e [Docker Desktop](https://docs.docker.com/get-started/get-docker/) no Windows/macOS, ou Docker Engine com Compose v2 no Linux. Inicie o Docker antes de continuar. No Windows, use PowerShell e containers Linux.

```bash
git --version
docker version
docker compose version
```

**Não é necessário instalar Python, Node.js, PostgreSQL ou um modelo generativo na máquina.** A primeira execução precisa de internet para baixar imagens, dependências e o modelo de embeddings.

O repositório é público: qualquer colega pode clonar o código sem convite ou autenticação no GitHub. Cada instalação gera suas próprias credenciais locais no passo seguinte.

## 2. Clonar e configurar

```bash
git clone https://github.com/Vinizada/pedidos-mcp-backend.git
cd pedidos-mcp-backend
```

Execute **um** dos comandos abaixo dentro da pasta clonada. Ele gera o `.env` com senha e chave aleatórias; uma configuração existente é preservada.

**Windows — PowerShell:**

```powershell
docker run --rm --mount "type=bind,source=${PWD},target=/workspace" -w /workspace python:3.13-slim python scripts/configurar_env.py
```

**macOS ou Linux — terminal:**

```bash
docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,source=${PWD},target=/workspace" -w /workspace python:3.13-slim python scripts/configurar_env.py
```

Se já tem Python 3, pode usar `python3 scripts/configurar_env.py` (Windows: `python scripts/configurar_env.py`). Nunca versione ou compartilhe o `.env`.

## 3. Iniciar e abrir a aplicação

```bash
docker compose up --build -d
docker compose ps
```

A primeira inicialização pode levar alguns minutos. O serviço `init` aplica migrações, popula o banco e indexa os produtos ativos. O backend inicia após essa etapa. Para acompanhar:

```bash
docker compose logs -f init
```

`Ctrl+C` encerra apenas a visualização dos logs. `init` terminar com **Exited (0)** é esperado; `backend` e `db` devem ficar **healthy**.

Abra **[http://localhost:8000/interface](http://localhost:8000/interface)**. No editor de texto, abra `.env`, copie apenas o valor à direita de `API_TOKEN=` e cole em **Chave de acesso**. Não inclua aspas nem `Bearer`. A chave fica somente na memória da página; atualizar ou fechar a aba exige entrar novamente.

| Endereço local | Finalidade |
|---|---|
| `/interface` | Produtos, clientes, documentos e pedidos |
| `/docs` | Documentação REST; use **Authorize** com a mesma chave |
| `/health/ready` | Retorna `{"status":"ready"}` quando banco e armazenamento estão prontos |
| `/mcp` | Endpoint Streamable HTTP; use um cliente MCP, não o navegador |

Cada instalação tem banco, arquivos e chave próprios. Os UUIDs do catálogo inicial são determinísticos; documentos e pedidos criados depois são locais.

## 4. Experimentar as telas

1. Em **Produtos**, pesquise `café`, `MER-001` ou um EAN e confira embalagem, preço e estoque.
2. Em **Clientes**, pesquise por nome ou código, como `CLI-001`.
3. Em **Documentos**, envie um arquivo de `data/pedidos/`.
4. O arquivo fica **Aguardando processamento** até o agente (seção 5) ou outro consumidor registrar o resultado.
5. Clique em **Atualizar** e abra o documento: o painel **Resultado do processamento** mostra o rascunho criado, as perguntas de esclarecimento ou o motivo da rejeição. Em **Aguardando esclarecimento**, o formulário **Resolver esclarecimento** traz cliente, produtos sugeridos e quantidades: confirme com o cliente, ajuste (ou busque outro produto no catálogo) e clique em **Criar rascunho com esta resolução**. O selo **Revisão obrigatória** indica rascunhos que pedem conferência prioritária; use o filtro acima das tabelas para listá-los. **Pedidos** também lista rascunhos criados sem documento.

Limites: **10 MiB** por arquivo; PDF com estrutura válida, de 1 a 100 páginas e sem senha; TXT em UTF-8; PNG ou JPEG conferidos pela assinatura. O backend armazena o original sem executar seu conteúdo; a leitura e a extração ficam no agente.

### Demonstração completa

Com a aplicação rodando:

```bash
docker compose exec backend python scripts/smoke_mcp.py --com-documento
```

O comando envia um TXT, lê seu conteúdo pelo MCP e cria um pedido com itens conhecidos do catálogo. É uma verificação técnica determinística, **não uma interpretação automática do arquivo**. Deve mostrar `"status": "ok"`, `"idempotencia": "ok"` e `"estoque_preservado": true`.

Clique em **Atualizar** na interface para ver o resultado. Cada execução com `--com-documento` cria um exemplo novo, identificado como demonstração. Sem essa opção, o script reutiliza um único rascunho para verificar a conexão MCP.

## 5. Processar documentos com IA

O agente roda em um container próprio (perfil `agente`), conversa com o backend apenas por MCP/REST e não recebe credenciais do banco nem os gabaritos.

1. Crie uma chave em [platform.claude.com](https://platform.claude.com) e preencha no `.env`:

   ```bash
   ANTHROPIC_API_KEY=sua-chave
   LLM_MODEL=claude-haiku-5-5   # escolhido na avaliação; claude-sonnet-5-5 e claude-opus-5-5 também funcionam
   ```

   Se o seu `.env` é anterior ao agente, rode o gerador de `.env` da seção 2 de novo: ele acrescenta as variáveis novas sem alterar valores existentes.

2. Processe automaticamente — o worker verifica os documentos pendentes a cada 30 s:

   ```bash
   docker compose --profile agente up -d --build agente
   docker compose logs -f agente
   ```

   Ou processe sob demanda, sem worker:

   ```bash
   docker compose --profile agente run --rm agente python -m extrator processar <documento_id>
   docker compose --profile agente run --rm agente python -m extrator processar --pendentes
   ```

3. Abra **Documentos** na interface e clique em **Atualizar**.

**Custo:** medido em 40 documentos (8/11/2026), cerca de **US$ 0,001 por documento com Haiku 5.5**, US$ 0,016 com Sonnet 5.5 e US$ 0,030 com Opus 5.5. Há teto por documento (`AGENTE_MAX_CUSTO_DOC_USD`, padrão US$ 0,50) e orçamento diário do worker (`AGENTE_ORCAMENTO_DIARIO_USD`, padrão US$ 5). Os logs trazem tokens, custo e latência por documento, nunca o conteúdo.

**O que o agente nunca faz:** confirmar compras, reservar ou baixar estoque, cadastrar clientes, trocar um produto por outro, arredondar quantidades ou seguir instruções escritas dentro do documento. Rascunhos continuam exigindo conferência humana.

Veja [arquitetura do agente](docs/arquitetura.md), [riscos e LGPD](docs/riscos-lgpd.md) e [avaliação](docs/avaliacao.md).

## 6. Conectar outro consumidor

- URL: `http://localhost:8000/mcp`.
- Transporte: **Streamable HTTP**.
- Header: `Authorization: Bearer <valor de API_TOKEN>`.
- O cliente precisa aceitar headers próprios; este perfil não implementa OAuth.

As ferramentas são `buscar_produtos`, `consultar_disponibilidade`, `buscar_cliente`, `validar_carrinho`, `criar_pedido_rascunho`, `registrar_analise` e `consultar_pedido`. `criar_pedido_rascunho` e `registrar_analise` gravam dados e devem ser chamadas pelo código do consumidor, não oferecidas a um modelo generativo. Os documentos estão disponíveis também como recursos MCP, inclusive o arquivo original.

Leia [Integração e contratos](docs/contratos-mcp.md) e os [schemas JSON](contracts/mcp-tools.json), exportados diretamente do servidor.

**Se o consumidor roda em outro container**, `localhost` aponta para esse container. Conecte-o à rede Docker `pedidos-mcp_default` e use `http://backend:8000/mcp`. Não é necessário publicar o backend na rede externa.

## 7. Executar os testes

```bash
docker compose --profile test run --build --rm tests
docker compose --profile test stop test-db
```

O comando constrói a imagem de testes e usa um PostgreSQL descartável separado. A suíte verifica contratos, integridade, uploads, autenticação, transações, preços, paginação, concorrência, ingestão, guardrails, orquestração e worker do agente — sempre com um modelo simulado, sem chamar a API real nem gastar créditos. O banco principal não é alterado. O resultado esperado é uma linha final `passed`, sem falhas.

O perfil de testes não é iniciado pelo `docker compose up -d` normal. Não precisa instalar ferramentas de desenvolvimento para executá-lo.

## Operação diária

| Ação | Comando |
|---|---|
| Iniciar novamente | `docker compose up -d` |
| Ver estado | `docker compose ps -a` |
| Ver erros recentes | `docker compose logs --tail 100 backend init` |
| Parar preservando os dados | `docker compose down` |
| Atualizar código | `git pull --ff-only`, depois `docker compose up --build -d` |
| Reindexar catálogo alterado | `docker compose run --rm init python -m pedidos.index_catalog` |

Os volumes guardam **banco**, **arquivos enviados** e **cache do modelo**. Não use `docker compose down -v` para atualizar: ele apaga os volumes. O seed não sobrescreve preços ou estoques já alterados.

### Configuração

| Variável no `.env` | Uso |
|---|---|
| `API_TOKEN` | Chave compartilhada, com pelo menos 32 caracteres |
| `POSTGRES_PASSWORD` | Senha inicial do banco; mantenha a gerada pelo script |
| `APP_PORT` | Porta externa; padrão `8000` |
| `SEARCH_MODE` | `hybrid`: texto + vetores; `lexical`: texto/trigramas |
| `ALLOWED_HOSTS` | Hosts aceitos, em array JSON |
| `ALLOWED_ORIGINS` | Origens de navegador aceitas, em array JSON |
| `ANTHROPIC_API_KEY` | Chave da API do Claude, usada só pelo agente |
| `LLM_MODEL` | Modelo do agente; recomendado `claude-haiku-5-5` (ver avaliação) |
| `LLM_EFFORT` | Profundidade de raciocínio: `low`, `medium` (padrão), `high`, `xhigh`, `max` |

Limites do agente (opcionais): `AGENTE_MAX_PAGINAS`, `AGENTE_MAX_CUSTO_DOC_USD`, `AGENTE_ORCAMENTO_DIARIO_USD`, `AGENTE_TIMEOUT_DOC_S`, `REVISAO_LIMITE_TOTAL_BRL`, `REVISAO_LIMITE_QUANTIDADE`, `AGENTE_MASCARAR_PII_TXT`. Os valores padrão estão em `src/extrator/config.py`.

O modo `hybrid` baixa e executa localmente embeddings multilíngues (~220 MB), sem chave de serviço externo. Para começar sem esse download, altere `SEARCH_MODE=lexical` antes de iniciar. Ao voltar para `hybrid`, rode `docker compose run --rm init` e `docker compose up -d` para indexar e recriar o backend com a configuração atualizada.

## Solução de problemas

| Sintoma | O que conferir |
|---|---|
| Não conecta ao Docker daemon | Abra o Docker Desktop; `docker version` deve mostrar Client e Server. |
| `API_TOKEN` ou senha obrigatória | Execute o gerador de `.env` na pasta do projeto. |
| Porta 8000 ocupada | Mude `APP_PORT=8001`; adicione `http://localhost:8001` e `http://127.0.0.1:8001` a `ALLOWED_ORIGINS`; rode `docker compose up -d` e abra a porta nova. |
| Porta de teste 55433 ocupada | Altere apenas a porta publicada de `test-db` no Compose, ou pare quem a ocupa. Os testes em container usam a porta interna 5432. |
| `init` terminou com erro | Leia `docker compose logs --tail 100 init`; confira internet, disco e configuração. Corrija, rode `docker compose run --rm init` e `docker compose up -d`. |
| Download do modelo falhou | Tente novamente com internet ou use `SEARCH_MODE=lexical`. |
| `401` ou chave inválida | Use a chave desta instalação; após alterá-la, rode `docker compose up -d`. |
| `403` ao usar outra porta | Atualize `ALLOWED_ORIGINS` para o endereço completo, incluindo protocolo e porta. |
| `INDICE_INDISPONIVEL` | Reindexe e confira se `EMBEDDING_MODEL` é o mesmo do índice. |
| Arquivo aguardando processamento | Upload não gera pedido sozinho. Suba o agente (seção 5), processe com `python -m extrator processar` ou execute a demonstração. |
| Agente não processa | `docker compose logs --tail 100 agente`: confira `ANTHROPIC_API_KEY`, se o orçamento diário foi atingido ou se o disjuntor abriu após falhas seguidas da API. |
| Situação **Falha no processamento** | O motivo aparece no painel do documento (recusa do modelo, teto de custo, tempo excedido). Falhas temporárias da API não são registradas: o documento continua pendente. |
| Arquivo rejeitado | Confira tamanho, estrutura, senha, páginas, codificação UTF-8 e se a extensão da imagem corresponde ao conteúdo. |
| Banco não autentica após editar a senha | Alterar `.env` não muda a senha de um volume existente. Restaure o valor anterior ou altere a senha no PostgreSQL. |

## Organização

```text
src/pedidos/
  app.py                 Inicialização HTTP e rotas principais
  dashboard_api.py       Listagens e operações de documentos
  http_security.py       Autenticação, limites e cabeçalhos HTTP
  service.py             Regras de catálogo e pedidos
  document_service.py    Armazenamento e vínculo dos documentos
  upload_validation.py   Validação estrutural de PDF/TXT/PNG/JPEG
  analise_service.py     Registro idempotente das análises do agente
  privacidade.py         Mascaramento de dados pessoais em textos livres
src/extrator/
  ingestao.py            PDF/TXT/imagem como blocos para o modelo; camada de texto
  extracao.py            Etapa 1: documento → itens extraídos (saída estruturada)
  guardrails_extracao.py Ancoragem de evidência, códigos e quantidade
  resolucao.py           Etapa 2: loop de ferramentas MCP de leitura
  guardrails_resolucao.py Proveniência, atributos, ambiguidade, cliente, coerência
  orquestrador.py        Decisão final, rascunho e registro da análise
  worker.py / cli.py     Processamento contínuo e manual
  mcp_server.py          Ferramentas e recursos MCP
  models.py / schemas.py Entidades relacionais e contratos tipados
  db.py / config.py      Conexões e configuração
  static/               Interface HTML, CSS e JavaScript
migrations/             Evolução versionada do PostgreSQL
tests/                  Testes unitários, integração e concorrência
contracts/              Schemas MCP exportados
data/pedidos/            Documentos de exemplo
data/gabaritos/          Resultados esperados, separados do processamento
scripts/                Configuração, demonstração e manutenção
```

Veja [modelo de dados](docs/modelo-dados.md), [desenvolvimento](docs/desenvolvimento.md) e [revisão técnica](docs/revisao-tecnica.md). Os dados de demonstração são sintéticos; gabaritos não são servidos pela API/MCP e não devem ser enviados ao consumidor avaliado. Além dos 20 casos originais há um conjunto escaneado (`data/pedidos/escaneados/`) e um de ataque (`data/pedidos/ataque/`); nenhum cobre a diversidade de layouts reais.

[Codespaces](docs/codespaces.md) e [hospedagem](docs/hospedagem.md) ficam documentados para uma etapa posterior. O fluxo recomendado neste momento é local.
