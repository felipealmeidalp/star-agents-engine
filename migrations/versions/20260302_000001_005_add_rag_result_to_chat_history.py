"""Add rag_result JSONB column to chat_history.

Revision ID: 005
Revises: 004
Create Date: 2026-03-02 00:00:01.000000

Stores the raw Qdrant search results when the RAG tool takes the FAQ path,
so they are not lost after summarization.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add rag_result JSONB column to chat_history (idempotent)."""
    conn = op.get_bind()

    # Check if column already exists
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'chat_history' AND column_name = 'rag_result'"
        )
    )
    if result.fetchone():
        return  # Column already exists

    op.add_column("chat_history", sa.Column("rag_result", JSONB(), nullable=True))


def downgrade() -> None:
    """Remove rag_result column from chat_history."""
    op.drop_column("chat_history", "rag_result")
