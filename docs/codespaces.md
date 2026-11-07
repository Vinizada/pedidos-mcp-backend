# Demonstração compartilhada com GitHub Codespaces

GitHub Pages hospeda arquivos estáticos e não executa este backend Python/PostgreSQL.
Codespaces disponibiliza um ambiente de desenvolvimento remoto onde o Docker Compose
pode rodar. Serve para sessões de integração e apresentação; não é um servidor 24/7.

## Custo e duração

Consulta às regras oficiais em 07/11/2026:

- Conta pessoal GitHub Free: **120 core-hours/mês**, equivalentes a **60 horas em
  máquina de 2 cores**, e **15 GB-mês de armazenamento**.
- Essa franquia é compartilhada com outros Codespaces da conta. Verifique o saldo
  na página de uso/faturamento antes de criar o ambiente.
- Sem forma de pagamento válida, o GitHub bloqueia o uso ao esgotar a franquia.
  Se já houver pagamento cadastrado, configure orçamento com bloqueio de uso para
  Codespaces; não habilite consumo pago para este trabalho.
- Computação é consumida enquanto o ambiente está ligado. Armazenamento continua
  contando enquanto ele existir, mesmo parado. Pare após cada sessão e exclua ao
  terminar o trabalho, depois de guardar o que precisar.
- O timeout padrão é 30 minutos sem atividade; prefira 15 minutos. Não existe
  mecanismo neste projeto para impedir a suspensão ou contornar limites gratuitos.

Fontes: [franquia por plano](https://docs.github.com/en/billing/reference/product-usage-included),
[cobrança e bloqueio](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces),
[timeout](https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces).

## Criar o ambiente

1. Abra [a criação de Codespace para este repositório](https://github.com/codespaces/new?repo=Vinizada%2Fpedidos-mcp-backend).
2. Selecione `main`, a configuração deste repositório e máquina de **2 cores**.
   Confirme na interface que há franquia disponível e nenhum consumo pago habilitado.
3. O ambiente configura `.env` com segredos aleatórios e inicia os containers.
   A primeira inicialização baixa dependências e embeddings; acompanhe no terminal:

```bash
docker compose logs -f init
docker compose ps
```

4. Quando `backend` estiver saudável, abra a aba **Ports** e localize a porta 8000.
   O hostname remoto é configurado automaticamente em `ALLOWED_HOSTS` e
   `ALLOWED_ORIGINS`, sem liberar qualquer domínio indiscriminadamente.

## Permitir que o grupo conecte seus agentes

A porta começa **privada**, acessível ao proprietário mediante autenticação GitHub.
Para clientes MCP externos do grupo, na aba Ports altere a visibilidade da porta
8000 para **Public**. Mantenha o protocolo interno da porta em **HTTP**: o backend
escuta HTTP e o GitHub fornece a URL externa HTTPS.

Copie o endereço encaminhado e acrescente `/mcp`. Compartilhe com os colegas:

- A URL HTTPS do MCP.
- O valor de `API_TOKEN` do `.env`, por um canal privado.
- O arquivo de contratos `contracts/mcp-tools.json` e as instruções de integração.

Mesmo com a porta pública, as ferramentas e a API exigem o token do backend. As rotas
de saúde e documentação são públicas. Nunca encaminhe a porta do banco de dados.
Sem a visibilidade Public, um cliente externo pode receber o login do GitHub em vez
da resposta MCP. Referência: [encaminhamento e compartilhamento de portas](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace).

Use **uma instância compartilhada** durante a sessão do grupo. Criar um Codespace por
colega cria bancos independentes. Quando a instância parar, a URL deixa de responder
até que o proprietário a inicie novamente.

## Dados e encerramento

Parar o Codespace preserva seus dados; excluir o Codespace remove o ambiente e o
armazenamento. Não trate os volumes como backup e guarde um dump antes de reconstruir
ou excluir. O guia [hospedagem.md](hospedagem.md) contém o comando de backup.
Para este trabalho, catálogo e índice podem ser regenerados; rascunhos criados depois
do seed precisam de backup se quiser preservá-los.

O arquivo `.devcontainer/devcontainer.json` foi preparado, mas uma instância remota
não foi criada/validada nesta entrega. A credencial CLI disponível não possui o
escopo `codespace`; a criação pode ser feita pelo link acima ou após autenticação
com essa permissão. Isso é independente da permissão `workflow` do GitHub Actions.
