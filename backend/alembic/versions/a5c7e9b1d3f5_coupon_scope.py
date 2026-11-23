"""coupon scope

Revision ID: a5c7e9b1d3f5
Revises: f4b6d8e0c2a3
Create Date: 2026-11-05 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a5c7e9b1d3f5'
down_revision: Union[str, Sequence[str], None] = 'f4b6d8e0c2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'coupons',
        sa.Column(
            'scope_type',
            sa.String(length=12),
            server_default='all',
            nullable=False,
        ),
    )
    op.add_column(
        'coupons',
        sa.Column(
            'scope_values',
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), 'postgresql'
            ),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f('ck_coupons_scope_type_valid'),
        'coupons',
        "scope_type IN ('all','category','sku')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_coupons_scope_type_valid'),
        'coupons',
        type_='check',
    )
    op.drop_column('coupons', 'scope_values')
    op.drop_column('coupons', 'scope_type')