"""Add Instagram columns to companies.

Revision ID: 010
Revises: 009
Create Date: 2026-10-01 00:00:00.000000

Configuração por Company da boas-vindas por comentário do Instagram.
instagram_account_id é a chave que resolve a Company no webhook de comentários, por
isso é única.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS instagram_account_id VARCHAR")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS instagram_access_token VARCHAR")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS instagram_public_reply TEXT")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_instagram_account_id "
        "ON companies (instagram_account_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_instagram_account_id")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS instagram_public_reply")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS instagram_access_token")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS instagram_account_id")
