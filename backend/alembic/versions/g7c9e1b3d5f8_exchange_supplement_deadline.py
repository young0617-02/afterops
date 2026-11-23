"""exchange supplement deadline

Revision ID: g7c9e1b3d5f8
Revises: f6b8d0e2c4a6
Create Date: 2026-11-12 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7c9e1b3d5f8'
down_revision: Union[str, Sequence[str], None] = 'f6b8d0e2c4a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_supplement_deadline',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE return_requests SET exchange_supplement_deadline = "
        "received_at + interval '72 hours' "
        "WHERE exchange_payment_status = 'pending' "
        "AND exchange_supplement_cents > 0 "
        "AND received_at IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('return_requests', 'exchange_supplement_deadline')
