"""Add instagram_welcomes table.

Revision ID: 011
Revises: 010
Create Date: 2026-10-01 00:00:01.000000

Uma linha por Comentário elegível processado. O índice único é parcial: registros
'falhou' não contam, para um novo comentário da mesma pessoa no mesmo post poder
tentar de novo.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = 'instagram_welcomes'")
    )
    if result.fetchone():
        return

    op.create_table(
        "instagram_welcomes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("comment_id", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("commenter_id", sa.Text(), nullable=False),
        sa.Column("commenter_username", sa.Text(), nullable=True),
        sa.Column("comment_text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("product", sa.Text(), nullable=True),
        sa.Column("dm_text", sa.Text(), nullable=True),
        sa.Column("recipient_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("matched_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_instagram_welcomes_company_media_commenter",
        "instagram_welcomes",
        ["company_id", "media_id", "commenter_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'falhou'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_instagram_welcomes_company_media_commenter", table_name="instagram_welcomes"
    )
    op.drop_table("instagram_welcomes")
