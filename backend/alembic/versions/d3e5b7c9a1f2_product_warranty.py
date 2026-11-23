"""product warranty

Revision ID: d3e5b7c9a1f2
Revises: c7d2a4e91b30
Create Date: 2026-11-05 05:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e5b7c9a1f2'
down_revision: Union[str, Sequence[str], None] = 'c7d2a4e91b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'products',
        sa.Column(
            'warranty_months',
            sa.Integer(),
            server_default='12',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_products_warranty_months_range'),
        'products',
        'warranty_months >= 0 AND warranty_months <= 120',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_products_warranty_months_range'),
        'products',
        type_='check',
    )
    op.drop_column('products', 'warranty_months')