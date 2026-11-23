"""return item inspection deduction

Revision ID: b1d3f5a7c9e2
Revises: a0b2c4e6f8d1
Create Date: 2026-11-09 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1d3f5a7c9e2'
down_revision: Union[str, Sequence[str], None] = 'a0b2c4e6f8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_items',
        sa.Column(
            'inspection_deduction_cents',
            sa.BigInteger(),
            server_default='0',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_return_items_inspection_deduction_nonnegative'),
        'return_items',
        'inspection_deduction_cents >= 0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_return_items_inspection_deduction_nonnegative'),
        'return_items',
        type_='check',
    )
    op.drop_column('return_items', 'inspection_deduction_cents')