# Pedidos MCP Backend

Backend acadêmico para um agente identificar produtos em pedidos PDF/TXT e preparar
**rascunhos de pedidos**. Inclui catálogo sintético, busca híbrida, validação comercial
e ferramentas MCP. A leitura de documentos, OCR, escolha/execução do LLM e avaliação
do agente pertencem aos demais integrantes do grupo.

## Iniciar com Docker

Pré-requisitos: Docker Engine/Desktop com Compose v2 e Python 3 para gerar `.env`.
Não precisa instalar PostgreSQL, modelo generativo nem cadastrar chave de LLM.

```bash
git clone https://github.com/Vinizada/pedidos-mcp-backend.git
cd pedidos-mcp-backend
python3 scripts/configurar_env.py
docker compose up --build -d
docker compose logs -f init
```

No Windows, use `python` no lugar de `python3`. O repositório é privado: o proprietário
precisa conceder acesso a cada integrante no GitHub.

O serviço `init` aplica migrações, insere 100 produtos/15 clientes e indexa 99 produtos
ativos. A primeira execução baixa um modelo de embeddings multilíngue (~220 MB);
as seguintes reutilizam o volume de cache. A API só inicia após o `init` terminar.
Não há chamada a LLM externo; os pesos de embeddings são baixados do Hugging Face.

- Documentação REST: <http://localhost:8000/docs> — clique em **Authorize** e informe
  o valor de `API_TOKEN` do seu `.env`, sem o prefixo `Bearer`.
- MCP Streamable HTTP: <http://localhost:8000/mcp> — configure o header
  `Authorization: Bearer <API_TOKEN>`. Não é uma página para abrir no navegador.
- Saúde: <http://localhost:8000/health/ready>.

Cada colega que roda localmente tem banco e token próprios. Uma instância hospedada
é necessária para compartilhar o mesmo banco. O PostgreSQL não publica porta no host;
a API local escuta apenas em `127.0.0.1`.

Para iniciar sem baixar embeddings, defina `SEARCH_MODE=lexical` no `.env` antes do
`docker compose up`. Esse modo recupera dados por full-text/trigramas; o modo padrão
`hybrid` combina essa busca com vetores reais do catálogo.

## Contrato para integrar o agente

| Ferramenta | Responsabilidade |
|---|---|
| `buscar_produtos` | EAN/SKU exato ou candidatos por descrição |
| `consultar_disponibilidade` | Preço vigente e estoque disponível |
| `buscar_cliente` | Código ou nome de cliente |
| `validar_carrinho` | Validar e calcular valores sem persistir |
| `criar_pedido_rascunho` | Gravar rascunho com idempotência |
| `consultar_pedido` | Recuperar pedido persistido |

Leia [os contratos e exemplos](docs/contratos-mcp.md), [os schemas JSON reais](contracts/mcp-tools.json)
e [o modelo de dados](docs/modelo-dados.md). O recurso MCP `pedidos://politicas` contém
as regras de uso. Não existe uma ferramenta de SQL livre ou de confirmação de compra.

O agente deve pedir esclarecimento sobre marca/embalagem/unidade ambígua. Scores são
ranking de recuperação, não probabilidade de identificação correta. Preço e total
vêm do banco; rascunhos **não reservam nem baixam estoque**. A confirmação humana e
a efetivação comercial são uma evolução futura, fora desta entrega.

## Verificar a conexão sem um agente

Instale Python 3.12/3.13 e [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync --frozen --no-editable
uv run --frozen --no-editable python scripts/smoke_mcp.py
```

O cliente realiza handshake MCP, lista as seis ferramentas, identifica cliente/produto,
valida, cria um rascunho e repete a operação. Deve retornar o mesmo pedido com estoque
preservado. Ele cria **um rascunho sintético** com chave `smoke-mcp-v1` e não usa LLM.
Para testar outra instância: acrescente `--url https://SEU-DOMINIO/mcp` e configure
o token dessa instância em `API_TOKEN` no seu ambiente.

## Testes do backend

```bash
docker compose --profile test up -d --wait test-db
uv run --frozen --no-editable python scripts/testar.py
```

O script usa o banco descartável `pedidos_test` na porta 55433. Não roda avaliação
de agente/LLM. Abrange validação, segurança HTTP, busca, preços, estoque, atomicidade,
snapshots e idempotência. Dados de cada teste são revertidos; testes concorrentes
usam chaves isoladas. `uv run --frozen --no-editable pytest` sem `TEST_DATABASE_URL`
executa os testes locais e marca a integração PostgreSQL como ignorada.

```bash
uv run --frozen --no-editable ruff check .
uv run --frozen --no-editable ruff format --check .
```

O modelo [docs/ci.github.yml](docs/ci.github.yml) executa a suíte com PostgreSQL real
e constrói a imagem. A automação ainda não está ativa: a credencial usada para criar
o repositório não possui a permissão GitHub `workflow`. Para ativar, copie o arquivo
para `.github/workflows/ci.yml` usando uma credencial com essa permissão ou o editor
do GitHub. A suíte já foi executada localmente contra PostgreSQL real.

## Exemplos para os demais integrantes

- `data/pedidos/`: 20 documentos (10 PDFs com texto selecionável e 10 TXTs).
- `data/gabaritos/`: resultados esperados e manifesto; **não fornecer ao agente**.
- `src/pedidos/fixtures.py`: fonte determinística do catálogo e clientes.

Os exemplos cobrem EANs, descrições, sinônimos, ambiguidade, produto desconhecido,
estoque zero, item inativo e quantidades inválidas. São sintéticos, não uma avaliação
concluída. Os PDFs usam um layout comum e não cobrem imagens escaneadas/OCR; o grupo
deve ampliar essa diversidade antes de afirmar robustez a layouts reais.

Para regenerar: `uv run --frozen --no-editable python scripts/gerar_exemplos.py`.
O gerador produz arquivos determinísticos e mantém gabaritos separados. Emails usam
`example.invalid`; EANs têm checksum válido demonstrativo, sem registro comercial GS1.

## Operação e desenvolvimento

```bash
docker compose ps
docker compose logs --tail 100 backend
docker compose run --rm init python -m pedidos.index_catalog
docker compose down
```

`down` preserva o banco e os modelos. **Não use `down -v` se quiser preservar os dados**.
O seed é idempotente e não redefine preços/estoques já alterados. Depois de editar o
catálogo, reindexe; em modo híbrido, um índice ausente/desatualizado retorna erro
explícito, sem fingir que a busca semântica está funcionando.

Mudanças no esquema exigem nova migração em `migrations/versions/`. O intervalo de
preços usa exclusão PostgreSQL para impedir vigências sobrepostas. Atualizações de
contratos: `uv run --frozen --no-editable python scripts/exportar_contratos.py`.

Dependências fixadas em `uv.lock`; o Docker usa `requirements.txt` com hashes.
Ao atualizá-las, regenere ambos: `uv lock` e
`uv export --frozen --no-dev --no-emit-project -o requirements.txt`.

## Hospedagem

Veja [o guia de implantação HTTPS](docs/hospedagem.md). O código está preparado para
ser hospedado, mas o repositório GitHub por si só não executa a aplicação.

Autenticação deste protótipo: segredo Bearer compartilhado para um grupo confiável.
Não implementa OAuth, isolamento entre usuários, quotas ou auditoria de produção.
Qualquer integrante com o token pode consultar os rascunhos da instância. Não usar
dados reais de clientes neste ambiente acadêmico.

## Referências técnicas

- [SDK Python oficial do MCP, linha 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [PostgreSQL + pgvector](https://github.com/pgvector/pgvector)
- [Modelos suportados pelo FastEmbed](https://qdrant.github.io/fastembed/examples/Supported_Models/)
- [Docker Compose](https://docs.docker.com/compose/gettingstarted/)
