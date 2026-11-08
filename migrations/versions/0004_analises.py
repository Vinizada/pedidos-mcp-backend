"""Análises registradas pelo agente consumidor e documentos de imagem."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_documento_tipo", "documentos", type_="check")
    op.create_check_constraint(
        "ck_documento_tipo",
        "documentos",
        "content_type IN ('application/pdf', 'text/plain', 'image/png', 'image/jpeg')",
    )
    op.create_table(
        "analises_documento",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "documento_id",
            sa.UUID(),
            sa.ForeignKey("documentos.id", name="fk_analise_documento", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decisao", sa.String(30), nullable=False),
        sa.Column(
            "pedido_id",
            sa.UUID(),
            sa.ForeignKey("pedido_cabecalho.id", name="fk_analise_pedido", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("perguntas", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("motivos", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("extracao", JSONB(), nullable=True),
        sa.Column("modelo", sa.String(100), nullable=False),
        sa.Column("versao_prompt", sa.String(40), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_saida", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_cache_leitura", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("custo_estimado", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latencia_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chave_idempotencia", sa.String(100), nullable=False, unique=True),
        sa.Column("hash_requisicao", sa.String(64), nullable=False),
        sa.Column("revisao_obrigatoria", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "motivos_revisao", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "guardrails_acionados", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("texto_verificavel", sa.Boolean(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "decisao IN ('criar_rascunho', 'pedir_esclarecimento', 'rejeitar', 'falha')",
            name="ck_analise_decisao",
        ),
        sa.CheckConstraint(
            "(decisao = 'criar_rascunho') = (pedido_id IS NOT NULL)",
            name="ck_analise_rascunho_pedido",
        ),
        sa.CheckConstraint(
            "decisao <> 'pedir_esclarecimento' OR jsonb_array_length(perguntas) >= 1",
            name="ck_analise_esclarecimento_pergunta",
        ),
        sa.CheckConstraint(
            "tokens_entrada >= 0 AND tokens_saida >= 0 AND tokens_cache_leitura >= 0"
            " AND custo_estimado >= 0 AND latencia_ms >= 0",
            name="ck_analise_metricas",
        ),
    )
    op.create_index(
        "ix_analises_documento_documento_criado",
        "analises_documento",
        ["documento_id", "criado_em"],
    )
    op.create_index("ix_analises_documento_pedido_id", "analises_documento", ["pedido_id"])


def downgrade():
    op.drop_table("analises_documento")
    op.drop_constraint("ck_documento_tipo", "documentos", type_="check")
    op.create_check_constraint(
        "ck_documento_tipo", "documentos", "content_type IN ('application/pdf', 'text/plain')"
    )
