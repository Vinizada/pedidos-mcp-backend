"""Esquema inicial congelado; não depende dos modelos ORM futuros."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    "CREATE EXTENSION IF NOT EXISTS btree_gist",
    "\n"
    "CREATE TABLE clientes (\n"
    "\tid UUID NOT NULL, \n"
    "\tcodigo VARCHAR(40) NOT NULL, \n"
    "\tnome VARCHAR(200) NOT NULL, \n"
    "\temail VARCHAR(254) NOT NULL, \n"
    "\tativo BOOLEAN NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (codigo)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE produtos (\n"
    "\tid UUID NOT NULL, \n"
    "\tsku VARCHAR(40) NOT NULL, \n"
    "\tean VARCHAR(13) NOT NULL, \n"
    "\tnome VARCHAR(200) NOT NULL, \n"
    "\tmarca VARCHAR(100) NOT NULL, \n"
    "\tcategoria VARCHAR(100) NOT NULL, \n"
    "\tunidade VARCHAR(8) NOT NULL, \n"
    "\tembalagem VARCHAR(200) NOT NULL, \n"
    "\tdescricao_busca TEXT NOT NULL, \n"
    "\tativo BOOLEAN NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_produtos_ean_formato CHECK (ean ~ '^[0-9]{13}$'), \n"
    "\tCONSTRAINT ck_produtos_unidade CHECK (unidade = 'UN'), \n"
    "\tUNIQUE (sku), \n"
    "\tUNIQUE (ean)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE estoque (\n"
    "\tproduto_id UUID NOT NULL, \n"
    "\tquantidade INTEGER NOT NULL, \n"
    "\treservado INTEGER NOT NULL, \n"
    "\tPRIMARY KEY (produto_id), \n"
    "\tCONSTRAINT ck_estoque_quantidade CHECK (quantidade >= 0), \n"
    "\tCONSTRAINT ck_estoque_reservado CHECK (reservado >= 0 AND reservado <= quantidade), \n"
    "\tFOREIGN KEY(produto_id) REFERENCES produtos (id)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE pedido_cabecalho (\n"
    "\tid UUID NOT NULL, \n"
    "\tcliente_id UUID NOT NULL, \n"
    "\tstatus VARCHAR(20) NOT NULL, \n"
    "\tcriado_em TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\ttotal NUMERIC(18, 2) NOT NULL, \n"
    "\tchave_idempotencia VARCHAR(100) NOT NULL, \n"
    "\thash_requisicao VARCHAR(64) NOT NULL, \n"
    "\treferencia_documento VARCHAR(200), \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_pedido_status CHECK (status = 'rascunho'), \n"
    "\tCONSTRAINT ck_pedido_total CHECK (total > 0), \n"
    "\tFOREIGN KEY(cliente_id) REFERENCES clientes (id), \n"
    "\tUNIQUE (chave_idempotencia)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_pedido_cabecalho_cliente_id ON pedido_cabecalho (cliente_id)",
    "\n"
    "CREATE TABLE precos (\n"
    "\tid UUID NOT NULL, \n"
    "\tproduto_id UUID NOT NULL, \n"
    "\tvalor NUMERIC(12, 2) NOT NULL, \n"
    "\tinicio_vigencia TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tfim_vigencia TIMESTAMP WITH TIME ZONE, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_precos_valor CHECK (valor > 0), \n"
    "\tCONSTRAINT ck_precos_vigencia CHECK (fim_vigencia IS NULL OR fim_vigencia > "
    "inicio_vigencia), \n"
    "\tFOREIGN KEY(produto_id) REFERENCES produtos (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_precos_produto_id ON precos (produto_id)",
    "\n"
    "CREATE TABLE produto_embeddings (\n"
    "\tproduto_id UUID NOT NULL, \n"
    "\tmodelo VARCHAR(200) NOT NULL, \n"
    "\thash_conteudo VARCHAR(64) NOT NULL, \n"
    "\tvetor VECTOR(384) NOT NULL, \n"
    "\tatualizado_em TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (produto_id), \n"
    "\tFOREIGN KEY(produto_id) REFERENCES produtos (id)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE pedido_itens (\n"
    "\tid UUID NOT NULL, \n"
    "\tpedido_id UUID NOT NULL, \n"
    "\tproduto_id UUID NOT NULL, \n"
    "\tean VARCHAR(13) NOT NULL, \n"
    "\tnome_produto VARCHAR(200) NOT NULL, \n"
    "\tunidade VARCHAR(8) NOT NULL, \n"
    "\tembalagem VARCHAR(200) NOT NULL, \n"
    "\tquantidade INTEGER NOT NULL, \n"
    "\tpreco_unitario NUMERIC(12, 2) NOT NULL, \n"
    "\tsubtotal NUMERIC(18, 2) NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_item_pedido_produto UNIQUE (pedido_id, produto_id), \n"
    "\tCONSTRAINT ck_item_quantidade CHECK (quantidade > 0), \n"
    "\tCONSTRAINT ck_item_preco CHECK (preco_unitario > 0), \n"
    "\tCONSTRAINT ck_item_subtotal CHECK (subtotal = quantidade * preco_unitario), \n"
    "\tFOREIGN KEY(pedido_id) REFERENCES pedido_cabecalho (id), \n"
    "\tFOREIGN KEY(produto_id) REFERENCES produtos (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_pedido_itens_pedido_id ON pedido_itens (pedido_id)",
    "ALTER TABLE precos ADD CONSTRAINT ex_precos_vigencia EXCLUDE USING gist (produto_id WITH =, "
    "tstzrange(inicio_vigencia, fim_vigencia, '[)') WITH &&)",
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    op.drop_table("pedido_itens")
    op.drop_table("produto_embeddings")
    op.drop_table("precos")
    op.drop_table("pedido_cabecalho")
    op.drop_table("estoque")
    op.drop_table("produtos")
    op.drop_table("clientes")
