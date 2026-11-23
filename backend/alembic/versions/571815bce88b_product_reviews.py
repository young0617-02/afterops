"""product reviews

Revision ID: 571815bce88b
Revises: b4fda5c9b198
Create Date: 2026-11-05 00:17:37.964630

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '571815bce88b'
down_revision: Union[str, Sequence[str], None] = 'b4fda5c9b198'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "product_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="visible",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5", name="rating_range"
        ),
        sa.CheckConstraint(
            "status IN ('visible','hidden')", name="status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_item_id", name="uq_product_reviews_order_item"
        ),
    )
    op.create_index(
        "ix_product_reviews_product_status",
        "product_reviews",
        ["product_id", "status", "created_at"],
    )
    op.create_index(
        "ix_product_reviews_merchant_status",
        "product_reviews",
        ["merchant_id", "status", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_product_reviews_merchant_status",
        table_name="product_reviews",
    )
    op.drop_index(
        "ix_product_reviews_product_status",
        table_name="product_reviews",
    )
    op.drop_table("product_reviews")
