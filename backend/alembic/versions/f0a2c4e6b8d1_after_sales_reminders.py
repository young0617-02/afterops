"""after-sales reminders

Revision ID: f0a2c4e6b8d1
Revises: e9a1b3d5f7c0
Create Date: 2026-11-10 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0a2c4e6b8d1'
down_revision: Union[str, Sequence[str], None] = 'e9a1b3d5f7c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_pickups',
        sa.Column('reminded_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_reminded_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'refunds',
        sa.Column(
            'settlement_reminded_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'return_deduction_appeals',
        sa.Column('reminded_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('return_deduction_appeals', 'reminded_at')
    op.drop_column('refunds', 'settlement_reminded_at')
    op.drop_column('repair_requests', 'quote_reminded_at')
    op.drop_column('return_pickups', 'reminded_at')
