# Desenvolvimento

O caminho principal de uso é Docker, descrito no README. Para trabalhar fora do container, use Python 3.12/3.13 e [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync --frozen --no-editable
docker compose --profile test up -d --wait test-db
uv run --frozen --no-editable python scripts/testar.py
uv run --frozen --no-editable ruff check .
uv run --frozen --no-editable ruff format --check .
docker compose --profile test stop test-db
```

Os testes exigem um banco terminado em `_test`. As credenciais desse serviço são exclusivas do ambiente descartável, não as credenciais do banco de uso.

## Convenções

- Rotas validam contratos Pydantic e delegam regras aos serviços.
- Cada operação abre uma sessão; a factory e o pool SQLAlchemy são reutilizados.
- Escritas encerram a transação ou executam rollback em falha.
- Dinheiro usa `Decimal`/`NUMERIC`; o consumidor não fornece preços.
- SQL recebe parâmetros; não há SQL livre exposto pela API/MCP.
- Identificadores seguem `snake_case`; classes, `PascalCase`.
- Erros de negócio usam `DomainError`; respostas não incluem SQL ou segredos.
- A interface usa JavaScript nativo, recursos locais e `textContent` para dados.
- A chave de acesso não é persistida no navegador nem embutida no HTML.

## Dependências

`uv.lock` fixa as versões. A imagem de execução instala `requirements.txt` com hashes; o estágio Docker `test` adiciona `requirements-dev.txt`. Ao alterar dependências:

```bash
uv lock
uv export --frozen --no-dev --no-emit-project -o requirements.txt
uv export --frozen --all-groups --no-emit-project -o requirements-dev.txt
```

O Dockerfile termina no estágio `runtime`, sem pytest/Ruff ou documentos de avaliação. Os uploads ficam em volume separado e não entram na imagem.

## Migrações

Não edite uma migração já aplicada. Crie outra revisão em `migrations/versions/`. O `init` executa `alembic upgrade head`; o health check compara a revisão do banco com a imagem. Valide em banco descartável antes de atualizar.

Checksum de EAN usa função SQL imutável. A exclusão de intervalos de preço é uma constraint PostgreSQL específica: revise-a manualmente ao gerar migrações. PKs e constraints UNIQUE já criam índices; não os duplique.

## Contratos e demonstrações

```bash
uv run --frozen --no-editable python scripts/exportar_contratos.py
uv run --frozen --no-editable python scripts/smoke_mcp.py
uv run --frozen --no-editable python scripts/gerar_exemplos.py
```

O cliente de exemplo aceita `--url` e `--com-documento`. A segunda opção verifica upload, leitura do recurso binário e vínculo de pedido. Ela cria um exemplo novo com itens definidos pelo script, sem interpretar o arquivo.

O modelo de CI está em [ci.github.yml](ci.github.yml). Para ativar, copie para `.github/workflows/ci.yml` com uma credencial que tenha a permissão `workflow`. Enquanto estiver em `docs`, ele não executa no GitHub Actions.

## Persistência dos documentos

O nome original é metadado; o nome físico é UUID. Os arquivos são gravados com acesso restrito, sincronizados no disco e associados ao banco. Falhas anteriores à tentativa de COMMIT removem apenas arquivos criados pela operação.

Uma falha de conexão durante COMMIT tem resultado potencialmente desconhecido: o banco pode ter confirmado a transação. Nesse caso, o original é preservado e o identificador aparece no log para conciliação. Podem restar arquivos órfãos após falhas abruptas; essa escolha evita apagar um arquivo já confirmado. Não há transação distribuída entre PostgreSQL e filesystem nem remoção automática de órfãos.

Backups devem incluir banco e volume de uploads. Catálogo e índice podem ser reconstruídos a partir das fontes sintéticas; uploads e pedidos não.

## Limites

Chave compartilhada para grupo confiável, sem isolamento por usuário, OAuth ou confirmação comercial. Uploads não iniciam processamento no backend: a leitura e a extração ficam no agente (`src/extrator`), que roda em container separado e é testado com modelo simulado (ver [arquitetura](arquitetura.md)). A validação estrutural não é antivírus.
