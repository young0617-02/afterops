"""order item warranty snapshot

Revision ID: e6f8c0d2b4a7
Revises: d3e5b7c9a1f2
Create Date: 2026-11-05 06:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f8c0d2b4a7'
down_revision: Union[str, Sequence[str], None] = 'd3e5b7c9a1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'order_items',
        sa.Column(
            'warranty_months',
            sa.Integer(),
            server_default='12',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_order_items_warranty_months_range'),
        'order_items',
        'warranty_months >= 0 AND warranty_months <= 120',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_order_items_warranty_months_range'),
        'order_items',
        type_='check',
    )
    op.drop_column('order_items', 'warranty_months')