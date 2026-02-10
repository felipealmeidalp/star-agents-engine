"""Baseline for existing database schema.

Revision ID: 001
Revises:
Create Date: 2025-01-21 00:00:00.000000

This is a baseline migration representing the existing database schema.
The database already has all tables created in Supabase.
This migration is marked as applied without running any operations.

To mark this migration as applied without executing:
    alembic stamp head

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Baseline - no operations needed.

    The following tables already exist in the database:
    - companies
    - users
    - agents
    - sub_agents
    - steps
    - decision_rules
    - sub_agent_connections
    - tools
    - tool_parameters
    - customers
    - chat_history
    - standard_messages
    - invitations
    """
    pass


def downgrade() -> None:
    """Baseline - cannot downgrade past initial schema."""
    pass
