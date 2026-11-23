"""inventory cost

Revision ID: b3d5f7a9c1e4
Revises: a2c4e6f8b0d3
Create Date: 2026-11-05 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d5f7a9c1e4'
down_revision: Union[str, Sequence[str], None] = 'a2c4e6f8b0d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'products',
        sa.Column(
            'cost_cents',
            sa.BigInteger(),
            server_default='0',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        'cost_nonnegative',
        'products',
        'cost_cents >= 0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'cost_nonnegative', 'products', type_='check'
    )
    op.drop_column('products', 'cost_cents')
