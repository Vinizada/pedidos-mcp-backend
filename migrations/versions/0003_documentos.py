"""Entradas PDF/TXT e vínculo persistente com os pedidos gerados."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documentos",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("nome_original", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(40), nullable=False, unique=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "tamanho_bytes > 0 AND tamanho_bytes <= 10485760", name="ck_documento_tamanho"
        ),
        sa.CheckConstraint(
            "content_type IN ('application/pdf', 'text/plain')", name="ck_documento_tipo"
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_documento_sha256"),
    )
    op.create_index("ix_documentos_sha256", "documentos", ["sha256"])
    op.create_index("ix_documentos_criado_em_id", "documentos", ["criado_em", "id"])
    op.add_column("pedido_cabecalho", sa.Column("documento_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_pedido_documento",
        "pedido_cabecalho",
        "documentos",
        ["documento_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_pedido_cabecalho_documento_id", "pedido_cabecalho", ["documento_id"])


def downgrade():
    op.drop_index("ix_pedido_cabecalho_documento_id", table_name="pedido_cabecalho")
    op.drop_constraint("fk_pedido_documento", "pedido_cabecalho", type_="foreignkey")
    op.drop_column("pedido_cabecalho", "documento_id")
    op.drop_table("documentos")
