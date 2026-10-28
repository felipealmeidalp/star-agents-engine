"""Add reasoning_effort column to sub_agents and prompts.

Revision ID: 009
Revises: 008
Create Date: 2026-09-29 00:00:00.000000

Permite fixar o reasoning effort ao lado do model, tanto no sub-agente quanto nos
prompts do RAG. Texto livre (none, low, medium, high, xhigh): a OpenAI valida e os
valores mudam por modelo.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sub_agents ADD COLUMN IF NOT EXISTS reasoning_effort TEXT")
    op.execute("ALTER TABLE prompts ADD COLUMN IF NOT EXISTS reasoning_effort TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE prompts DROP COLUMN IF EXISTS reasoning_effort")
    op.execute("ALTER TABLE sub_agents DROP COLUMN IF EXISTS reasoning_effort")
