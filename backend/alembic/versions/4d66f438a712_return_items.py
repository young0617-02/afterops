"""return items

Revision ID: 4d66f438a712
Revises: 580caaf644e6
Create Date: 2026-11-04 21:49:08.152489

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d66f438a712'
down_revision: Union[str, Sequence[str], None] = '580caaf644e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "return_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("return_request_id", sa.BigInteger(), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("title_snapshot", sa.String(length=255), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("qty > 0", name="qty_positive"),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["return_request_id"],
            ["return_requests.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "return_request_id",
            "order_item_id",
            name="uq_return_items_request_item",
        ),
    )
    op.execute(
        """
        INSERT INTO return_items
            (return_request_id, order_item_id, sku, title_snapshot, qty)
        SELECT rr.id, oi.id, oi.sku, oi.title_snapshot, oi.qty
        FROM return_requests rr
        JOIN order_items oi ON oi.order_id = rr.order_id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("return_items")
