"""refund settlement retries

Revision ID: e5a7c9d1b3f6
Revises: d4f6a8c0e2b4
Create Date: 2026-11-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a7c9d1b3f6'
down_revision: Union[str, Sequence[str], None] = 'd4f6a8c0e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'refunds',
        sa.Column(
            'settlement_failed_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE refunds SET settlement_failed_at = "
        "COALESCE(executed_at, created_at, now()) "
        "WHERE settlement_status = 'failed'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('refunds', 'settlement_failed_at')
