"""Add category to instagram_welcomes.

Revision ID: 012
Revises: 011
Create Date: 2026-10-08 00:00:00.000000

A Categoria do comentário de cada boas-vindas, gravada na reserva, para o casamento
saber quando é reclamação. Nula nas linhas antigas.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE instagram_welcomes ADD COLUMN IF NOT EXISTS category TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE instagram_welcomes DROP COLUMN IF EXISTS category")
