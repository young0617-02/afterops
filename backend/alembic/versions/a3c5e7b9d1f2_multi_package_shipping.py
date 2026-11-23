"""multi package shipping

Revision ID: a3c5e7b9d1f2
Revises: f2b4d6e8a0c1
Create Date: 2026-11-09 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c5e7b9d1f2'
down_revision: Union[str, Sequence[str], None] = 'f2b4d6e8a0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('uq_shipments_order_id', 'shipments', type_='unique')
    op.add_column(
        'shipments',
        sa.Column(
            'package_no',
            sa.Integer(),
            server_default='1',
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        'uq_shipments_order_package',
        'shipments',
        ['order_id', 'package_no'],
    )
    op.create_table(
        'shipment_items',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('shipment_id', sa.BigInteger(), nullable=False),
        sa.Column('order_item_id', sa.BigInteger(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.CheckConstraint('qty > 0', name=op.f('ck_shipment_items_qty_positive')),
        sa.ForeignKeyConstraint(
            ['order_item_id'],
            ['order_items.id'],
            name=op.f('fk_shipment_items_order_item_id_order_items'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['shipment_id'],
            ['shipments.id'],
            name=op.f('fk_shipment_items_shipment_id_shipments'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_shipment_items')),
        sa.UniqueConstraint(
            'shipment_id',
            'order_item_id',
            name='uq_shipment_items_shipment_item',
        ),
    )
    op.create_index(
        'ix_shipment_items_order_item',
        'shipment_items',
        ['order_item_id'],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO shipment_items (shipment_id, order_item_id, qty)
        SELECT shipments.id, order_items.id, order_items.qty
        FROM shipments
        JOIN order_items ON order_items.order_id = shipments.order_id
        WHERE NOT EXISTS (
            SELECT 1 FROM shipment_items
            WHERE shipment_items.shipment_id = shipments.id
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_shipment_items_order_item', table_name='shipment_items')
    op.drop_table('shipment_items')
    op.drop_constraint(
        'uq_shipments_order_package', 'shipments', type_='unique'
    )
    duplicates = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT order_id FROM shipments GROUP BY order_id HAVING COUNT(*) > 1"
            ") multi_package_orders"
        )
    ).scalar_one()
    if duplicates:
        raise RuntimeError(
            "Cannot downgrade multi-package shipping while orders have multiple "
            f"packages ({duplicates} orders). Merge or remove extra packages first."
        )
    op.drop_column('shipments', 'package_no')
    op.create_unique_constraint('uq_shipments_order_id', 'shipments', ['order_id'])
