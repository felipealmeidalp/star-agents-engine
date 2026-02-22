"""Add send_content_before_execution column to tools table.

Revision ID: 003
Revises: 002
Create Date: 2026-02-21 00:00:00.000000

Adds send_content_before_execution boolean column to tools table.
When true, the assistant's text content is sent to the lead BEFORE
executing the tool's HTTP call.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add send_content_before_execution column to tools table."""
    op.add_column(
        "tools",
        sa.Column(
            "send_content_before_execution",
            sa.Boolean,
            nullable=True,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    """Remove send_content_before_execution column from tools table."""
    op.drop_column("tools", "send_content_before_execution")
