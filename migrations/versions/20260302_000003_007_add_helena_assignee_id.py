"""Add helena_assignee_id to companies.

Revision ID: 007
Revises: 006
Create Date: 2026-03-02 00:00:03.000000

Adds companies.helena_assignee_id — the userId (a UUID Helena hands us as text)
of the fixed attendant that transfer_to_human assigns a Helena session to. Stored
as plain String, mirroring helena_apikey (we pass it straight into the assignee
body), not the UUID column type of helena_token.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add helena_assignee_id column (idempotent)."""
    conn = op.get_bind()

    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'companies' AND column_name = 'helena_assignee_id'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "companies", sa.Column("helena_assignee_id", sa.String(), nullable=True)
        )


def downgrade() -> None:
    """Remove the helena_assignee_id column."""
    op.drop_column("companies", "helena_assignee_id")
