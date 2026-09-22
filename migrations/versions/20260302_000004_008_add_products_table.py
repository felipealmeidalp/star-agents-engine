"""Add products table.

Revision ID: 008
Revises: 007
Create Date: 2026-03-02 00:00:04.000000

Cria products: catálogo por-marca sincronizado do 7ball.com.br. O id vem de uma
serial própria (o wpId repete entre CPTs/marcas, então não serve de PK); wpId +
type + isMotherCategory + company_id garantem unicidade real. As colunas usam o
mesmo camelCase do resto do banco, mapeado pra snake_case no ORM.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the products table and its type enum (idempotent)."""
    conn = op.get_bind()

    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = 'products'")
    )
    if result.fetchone():
        return

    product_type = sa.Enum("sinuca", "fitness", name="product_type")
    product_type.create(conn, checkfirst=True)

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column("wpId", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("imageUrl", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("motherCategory", sa.String(), nullable=True),
        sa.Column("motherCategorySlug", sa.String(), nullable=True),
        sa.Column(
            "isMotherCategory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("type", product_type, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "company_id", "type", "wpId", "isMotherCategory",
            name="uq_products_company_type_wp",
        ),
    )


def downgrade() -> None:
    """Drop the products table and its enum type."""
    op.drop_table("products")
    sa.Enum(name="product_type").drop(op.get_bind(), checkfirst=True)
