"""partial cancellation

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7b9d1f2
Create Date: 2026-11-09 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d6f8a0c2e4'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7b9d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'order_items',
        sa.Column(
            'cancelled_qty',
            sa.Integer(),
            server_default='0',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_order_items_cancelled_qty_range'),
        'order_items',
        'cancelled_qty >= 0 AND cancelled_qty <= qty',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_order_items_cancelled_qty_range'),
        'order_items',
        type_='check',
    )
    op.drop_column('order_items', 'cancelled_qty')
