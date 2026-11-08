"""Pendências estruturadas para resolução humana de esclarecimentos."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analises_documento", sa.Column("pendencias", JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_analise_pendencias_esclarecimento",
        "analises_documento",
        "pendencias IS NULL OR decisao = 'pedir_esclarecimento'",
    )


def downgrade():
    op.drop_constraint("ck_analise_pendencias_esclarecimento", "analises_documento", type_="check")
    op.drop_column("analises_documento", "pendencias")
