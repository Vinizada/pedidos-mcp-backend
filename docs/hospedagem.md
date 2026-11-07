# Hospedagem compartilhada

O backend local já funciona com Docker. A publicação remota depende de um servidor
ou provedor escolhido pelo grupo e, para HTTPS, um domínio. Não há instância remota
provisionada por este repositório.

## Caminho com servidor Docker

1. Disponibilize um servidor Linux com Docker/Compose, armazenamento persistente
   e acesso de saída para baixar imagem/modelo. Comece com 2 GB de RAM e monitore
   o consumo; é uma referência inicial para a demonstração, não capacidade garantida.
2. Aponte o DNS de um domínio ao servidor; libere somente HTTPS/HTTP (443/80) e o
   acesso administrativo necessário. PostgreSQL continua na rede interna Docker.
3. Clone o repositório privado e execute `python3 scripts/configurar_env.py`.
4. Configure no `.env`:

```dotenv
PUBLIC_DOMAIN=pedidos.seudominio.com
ALLOWED_HOSTS=["pedidos.seudominio.com","localhost","127.0.0.1","backend"]
ALLOWED_ORIGINS=["https://pedidos.seudominio.com"]
```

5. Inicie o Compose base mais o proxy:

```bash
docker compose -f compose.yaml -f deploy/compose.https.yaml up --build -d
```

O proxy Caddy emite o certificado automaticamente quando o DNS e as portas estão
corretos. Os colegas usam `https://pedidos.seudominio.com/mcp` com o token da instância.
Compartilhe o token por canal privado; não inclua `.env` no GitHub.
Referência: [requisitos de HTTPS automático do Caddy](https://caddyserver.com/docs/automatic-https).

## Caminho com plataforma gerenciada

Use o Dockerfile em um serviço de aplicação e PostgreSQL com extensão `vector`
disponível. O usuário de migração precisa poder habilitar `vector`, `unaccent`,
`pg_trgm` e `btree_gist`. Configure `DATABASE_URL`, `API_TOKEN`, `SEARCH_MODE`,
`ALLOWED_HOSTS`, `ALLOWED_ORIGINS` e um cache gravável para embeddings. Execute
`python -m pedidos.bootstrap` antes do processo web.

A aplicação escuta na porta 8000. Configure essa porta no provedor; se ele exigir
`PORT` dinâmico, ajuste o comando de inicialização explicitamente. Use um disco/volume
persistente para o banco e preferencialmente para o cache dos pesos.

## Backup e atualização

Backup lógico (não coloca a senha no comando):

```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup.sql
```

Guarde backups fora do repositório. Antes de atualizar, faça backup, obtenha os commits
e rode novamente o Compose com `--build`; o serviço `init` aplica migrações e reindexa
somente produtos alterados. Não apague volumes para aplicar uma atualização.

O perfil de segurança é para demonstração privada com dados sintéticos. Publicação
aberta ou uso com clientes reais exige autorização por usuário, auditoria, quotas,
gestão de segredos e revisão das regras de negócio.
