"""Reforça integridade de EAN e índices de relacionamento/listagem."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # Validação depende somente do argumento: não usa tabela nem configuração mutável.
    # O formato é testado antes dos casts para que entrada malformada retorne false.
    op.execute("""
        CREATE FUNCTION ean13_valido(codigo text)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
            SELECT CASE
                WHEN codigo ~ '^[0-9]{13}$' THEN (
                    SELECT sum(
                        substring(codigo FROM posicao FOR 1)::integer
                        * CASE WHEN posicao % 2 = 0 THEN 3 ELSE 1 END
                    ) % 10 = 0
                    FROM generate_series(1, 13) AS digitos(posicao)
                )
                ELSE false
            END
        $$
    """)
    op.create_check_constraint("ck_produtos_ean_checksum", "produtos", "ean13_valido(ean)")
    # O índice UNIQUE (pedido_id, produto_id) atende consultas pelo pedido,
    # mas não substitui um índice cujo primeiro campo seja produto_id.
    op.create_index("ix_pedido_itens_produto_id", "pedido_itens", ["produto_id"])
    # A PK desempata datas iguais e permite paginação com ordenação estável.
    op.create_index("ix_pedido_cabecalho_criado_em_id", "pedido_cabecalho", ["criado_em", "id"])


def downgrade():
    op.drop_index("ix_pedido_cabecalho_criado_em_id", table_name="pedido_cabecalho")
    op.drop_index("ix_pedido_itens_produto_id", table_name="pedido_itens")
    op.drop_constraint("ck_produtos_ean_checksum", "produtos", type_="check")
    op.execute("DROP FUNCTION ean13_valido(text)")
