"""Add unique constraint on cw_contact_id.

Revision ID: 004
Revises: 003
Create Date: 2026-03-02 00:00:00.000000

Fixes race condition where concurrent webhooks create duplicate customers
for the same cw_contact_id. This migration:
1. Soft-deletes duplicate rows (keeps the oldest per cw_contact_id)
2. Creates a partial unique index to prevent future duplicates

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add partial unique index on cw_contact_id after cleaning duplicates (idempotent)."""
    conn = op.get_bind()

    # Verificar se o index já existe
    result = conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_customers_cw_contact_id_unique'"
    ))
    if result.fetchone():
        return  # Index já existe, nada a fazer

    # Passo 1: Soft-delete duplicatas (manter o mais antigo por cw_contact_id)
    op.execute(text("""
        UPDATE customers
        SET deleted_at = NOW()
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY cw_contact_id
                           ORDER BY id ASC
                       ) AS rn
                FROM customers
                WHERE cw_contact_id IS NOT NULL
                  AND deleted_at IS NULL
            ) ranked
            WHERE rn > 1
        )
    """))

    # Passo 2: Criar partial unique index
    # - Exclui NULLs (customers que nao vem do Chatwoot)
    # - Exclui soft-deleted (permite recriar apos exclusao)
    op.create_index(
        "ix_customers_cw_contact_id_unique",
        "customers",
        ["cw_contact_id"],
        unique=True,
        postgresql_where=text("cw_contact_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Remove the partial unique index."""
    op.drop_index("ix_customers_cw_contact_id_unique", table_name="customers")
