"""inventory movements

Revision ID: 7036da747308
Revises: 9c13ce47fd0d
Create Date: 2026-11-04 23:17:48.488101

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7036da747308'
down_revision: Union[str, Sequence[str], None] = '9c13ce47fd0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.String(length=64), nullable=False),
        sa.Column(
            "warehouse_name",
            sa.String(length=120),
            server_default="",
            nullable=False,
        ),
        sa.Column("qty_delta", sa.Integer(), nullable=False),
        sa.Column("qty_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("return_id", sa.BigInteger(), nullable=True),
        sa.Column("operator_user_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "qty_after >= 0", name="qty_after_nonnegative"
        ),
        sa.CheckConstraint("qty_delta != 0", name="qty_delta_nonzero"),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["operator_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["return_id"], ["return_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_movements_merchant_product",
        "inventory_movements",
        ["merchant_id", "product_id", "created_at"],
    )
    op.create_index(
        "ix_inventory_movements_order",
        "inventory_movements",
        ["order_id"],
    )
    op.execute(
        """
        INSERT INTO inventory_movements
            (merchant_id, product_id, warehouse_id, warehouse_name,
             qty_delta, qty_after, reason, note)
        SELECT merchant_id, product_id, warehouse_id, warehouse_name,
               qty_available, qty_available, 'opening_balance', '迁移回填期初库存'
        FROM inventory
        WHERE qty_available > 0
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_inventory_movements_order", table_name="inventory_movements"
    )
    op.drop_index(
        "ix_inventory_movements_merchant_product",
        table_name="inventory_movements",
    )
    op.drop_table("inventory_movements")
