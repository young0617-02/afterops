"""return item exchange

Revision ID: e6f8a0c2d4b7
Revises: d5f7a9c1e3b6
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f8a0c2d4b7'
down_revision: Union[str, Sequence[str], None] = 'd5f7a9c1e3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_items',
        sa.Column('exchange_sku', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'return_items',
        sa.Column(
            'exchange_unit_price_cents', sa.BigInteger(), nullable=True
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('return_items', 'exchange_unit_price_cents')
    op.drop_column('return_items', 'exchange_sku')
