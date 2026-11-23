"""return inspection deduction

Revision ID: c5e7a9b1d3f6
Revises: b4d6f8a0c2e4
Create Date: 2026-11-09 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e7a9b1d3f6'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column(
            'inspection_deduction_cents',
            sa.BigInteger(),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column(
        'return_requests',
        sa.Column(
            'inspection_note',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_return_requests_inspection_deduction_nonnegative'),
        'return_requests',
        'inspection_deduction_cents >= 0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_return_requests_inspection_deduction_nonnegative'),
        'return_requests',
        type_='check',
    )
    op.drop_column('return_requests', 'inspection_note')
    op.drop_column('return_requests', 'inspection_deduction_cents')
