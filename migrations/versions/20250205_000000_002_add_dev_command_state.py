"""Add dev_command_state column to customers table.

Revision ID: 002
Revises: 001
Create Date: 2025-02-05 00:00:00.000000

Adds dev_command_state JSONB column to customers table for multi-step
dev commands like #mudar_agente.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add dev_command_state column to customers table."""
    op.add_column(
        "customers",
        sa.Column("dev_command_state", JSON, nullable=True),
    )


def downgrade() -> None:
    """Remove dev_command_state column from customers table."""
    op.drop_column("customers", "dev_command_state")
