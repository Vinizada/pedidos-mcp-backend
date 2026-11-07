# Pedidos

Catálogo de produtos e clientes, recebimento de documentos e criação de rascunhos de pedidos. A aplicação reúne interface web, API REST, servidor MCP e PostgreSQL.

O processamento dos documentos é integrado separadamente pelo grupo. Enviar um arquivo o disponibiliza para processamento; o pedido aparece quando o consumidor o cria pela API ou pelo MCP, informando o `documento_id` do upload.

## Recursos

- Produtos e clientes com pesquisa e paginação.
- Upload de PDF/TXT e consulta dos pedidos vinculados.
- Busca de produtos por EAN/SKU, texto e similaridade semântica.
- Validação de cliente, preço, disponibilidade, unidade e quantidade.
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
4. O arquivo fica **Aguardando processamento** até o consumidor externo produzir um pedido.
5. Depois da criação, clique em **Atualizar** e abra o pedido vinculado. **Pedidos** também lista rascunhos criados sem documento.

Limites: **10 MiB** por arquivo; PDF com estrutura válida, de 1 a 100 páginas e sem senha; TXT em UTF-8. O backend armazena o original, sem executar conteúdo, OCR ou extração de itens.

### Demonstração completa

Com a aplicação rodando:

```bash
docker compose exec backend python scripts/smoke_mcp.py --com-documento
```

O comando envia um TXT, lê seu conteúdo pelo MCP e cria um pedido com itens conhecidos do catálogo. É uma verificação técnica determinística, **não uma interpretação automática do arquivo**. Deve mostrar `"status": "ok"`, `"idempotencia": "ok"` e `"estoque_preservado": true`.

Clique em **Atualizar** na interface para ver o resultado. Cada execução com `--com-documento` cria um exemplo novo, identificado como demonstração. Sem essa opção, o script reutiliza um único rascunho para verificar a conexão MCP.

## 5. Conectar o processamento

- URL: `http://localhost:8000/mcp`.
- Transporte: **Streamable HTTP**.
- Header: `Authorization: Bearer <valor de API_TOKEN>`.
- O cliente precisa aceitar headers próprios; este perfil não implementa OAuth.

As ferramentas são `buscar_produtos`, `consultar_disponibilidade`, `buscar_cliente`, `validar_carrinho`, `criar_pedido_rascunho` e `consultar_pedido`. Os documentos estão disponíveis também como recursos MCP, inclusive o arquivo original.

Leia [Integração e contratos](docs/contratos-mcp.md) e os [schemas JSON](contracts/mcp-tools.json), exportados diretamente do servidor.

**Se o consumidor roda em outro container**, `localhost` aponta para esse container. Conecte-o à rede Docker `pedidos-mcp_default` e use `http://backend:8000/mcp`. Não é necessário publicar o backend na rede externa.

## 6. Executar os testes

```bash
docker compose --profile test run --build --rm tests
docker compose --profile test stop test-db
```

O comando constrói a imagem de testes e usa um PostgreSQL descartável separado. A suíte verifica contratos, integridade, uploads, autenticação, transações, preços, paginação e concorrência. O banco principal não é alterado. O resultado esperado é uma linha final `passed`, sem falhas.

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
| Arquivo aguardando processamento | Upload não gera pedido sozinho. Integre o consumidor ou execute a demonstração. |
| PDF/TXT rejeitado | Confira tamanho, estrutura, senha, páginas e codificação UTF-8. |
| Banco não autentica após editar a senha | Alterar `.env` não muda a senha de um volume existente. Restaure o valor anterior ou altere a senha no PostgreSQL. |

## Organização

```text
src/pedidos/
  app.py                 Inicialização HTTP e rotas principais
  dashboard_api.py       Listagens e operações de documentos
  http_security.py       Autenticação, limites e cabeçalhos HTTP
  service.py             Regras de catálogo e pedidos
  document_service.py    Armazenamento e vínculo dos documentos
  upload_validation.py   Validação estrutural de PDF/TXT
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

Veja [modelo de dados](docs/modelo-dados.md), [desenvolvimento](docs/desenvolvimento.md) e [revisão técnica](docs/revisao-tecnica.md). Os dados de demonstração são sintéticos; gabaritos não são servidos pela API/MCP e não devem ser enviados ao consumidor avaliado. Os PDFs de exemplo não cobrem OCR ou diversidade de layouts reais.

[Codespaces](docs/codespaces.md) e [hospedagem](docs/hospedagem.md) ficam documentados para uma etapa posterior. O fluxo recomendado neste momento é local.
