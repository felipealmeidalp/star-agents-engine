"""Add Helena channel columns to companies.

Revision ID: 006
Revises: 005
Create Date: 2026-03-02 00:00:02.000000

Adds the two per-tenant Helena identifiers to companies: helena_token (the UUID
that appears in the webhook URL, matched to route the tenant, like cw_token) and
helena_apikey (the Bearer used to send replies back to Helena).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add helena_token/helena_apikey columns and the token index (idempotent)."""
    conn = op.get_bind()

    # helena_token column
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'companies' AND column_name = 'helena_token'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "companies", sa.Column("helena_token", UUID(as_uuid=True), nullable=True)
        )

    # helena_apikey column
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'companies' AND column_name = 'helena_apikey'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "companies", sa.Column("helena_apikey", sa.String(), nullable=True)
        )

    # Unique index on helena_token (matches ORM index=True, unique=True)
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_companies_helena_token'"
        )
    )
    if not result.fetchone():
        op.create_index(
            "ix_companies_helena_token",
            "companies",
            ["helena_token"],
            unique=True,
        )


def downgrade() -> None:
    """Remove the Helena token index and both columns."""
    op.drop_index("ix_companies_helena_token", table_name="companies")
    op.drop_column("companies", "helena_apikey")
    op.drop_column("companies", "helena_token")
