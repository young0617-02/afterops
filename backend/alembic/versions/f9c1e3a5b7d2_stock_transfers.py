"""stock transfer orders

Revision ID: f9c1e3a5b7d2
Revises: e7a9c1d3f5b8
Create Date: 2026-11-09 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9c1e3a5b7d2'
down_revision: Union[str, Sequence[str], None] = 'e7a9c1d3f5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'stock_transfers',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('transfer_no', sa.String(length=32), nullable=False),
        sa.Column('product_id', sa.BigInteger(), nullable=False),
        sa.Column('sku', sa.String(length=64), nullable=False),
        sa.Column(
            'title_snapshot',
            sa.String(length=255),
            server_default='',
            nullable=False,
        ),
        sa.Column('from_warehouse_id', sa.String(length=64), nullable=False),
        sa.Column('to_warehouse_id', sa.String(length=64), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.String(length=16),
            server_default='in_transit',
            nullable=False,
        ),
        sa.Column('note', sa.Text(), server_default='', nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('received_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'received_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column('cancelled_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'cancelled_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            'cancel_reason',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint('qty > 0', name=op.f('ck_stock_transfers_qty_positive')),
        sa.CheckConstraint(
            "status IN ('in_transit','received','cancelled')",
            name=op.f('ck_stock_transfers_status_valid'),
        ),
        sa.ForeignKeyConstraint(
            ['created_by'],
            ['users.id'],
            name=op.f('fk_stock_transfers_created_by_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['cancelled_by'],
            ['users.id'],
            name=op.f('fk_stock_transfers_cancelled_by_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['merchant_id'],
            ['merchants.id'],
            name=op.f('fk_stock_transfers_merchant_id_merchants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['product_id'],
            ['products.id'],
            name=op.f('fk_stock_transfers_product_id_products'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['received_by'],
            ['users.id'],
            name=op.f('fk_stock_transfers_received_by_users'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_transfers')),
        sa.UniqueConstraint(
            'merchant_id',
            'transfer_no',
            name='uq_stock_transfers_merchant_no',
        ),
    )
    op.create_index(
        'ix_stock_transfers_merchant_status',
        'stock_transfers',
        ['merchant_id', 'status', 'updated_at'],
        unique=False,
    )
    op.create_index(
        'ix_stock_transfers_product',
        'stock_transfers',
        ['product_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_stock_transfers_product', table_name='stock_transfers'
    )
    op.drop_index(
        'ix_stock_transfers_merchant_status', table_name='stock_transfers'
    )
    op.drop_table('stock_transfers')