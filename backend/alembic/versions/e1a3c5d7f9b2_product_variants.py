"""product variants

Revision ID: e1a3c5d7f9b2
Revises: d0f2a4c6e8b1
Create Date: 2026-11-06 05:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a3c5d7f9b2'
down_revision: Union[str, Sequence[str], None] = 'd0f2a4c6e8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'products',
        sa.Column(
            'variant_group',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'products',
        sa.Column(
            'variant_label',
            sa.String(length=80),
            server_default='',
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'variant_label')
    op.drop_column('products', 'variant_group')
