"""refund settlements

Revision ID: c7e9a1b3d5f8
Revises: f5a7c9e1b3d5
Create Date: 2026-11-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e9a1b3d5f8'
down_revision: Union[str, Sequence[str], None] = 'f5a7c9e1b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'refunds',
        sa.Column(
            'settlement_status',
            sa.String(length=16),
            server_default='succeeded',
            nullable=False,
        ),
    )
    op.add_column(
        'refunds',
        sa.Column(
            'settlement_reference',
            sa.String(length=128),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'refunds',
        sa.Column(
            'settlement_failure_reason',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'refunds',
        sa.Column(
            'settlement_attempts',
            sa.Integer(),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column(
        'refunds',
        sa.Column(
            'settled_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "ALTER TABLE refunds ADD CONSTRAINT "
        "ck_refunds_settlement_status_valid CHECK (settlement_status IN "
        "('processing','succeeded','failed'))"
    )
    op.execute(
        "ALTER TABLE refunds ADD CONSTRAINT "
        "ck_refunds_settlement_attempts_nonnegative CHECK ("
        "settlement_attempts >= 0)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE refunds "
        "DROP CONSTRAINT ck_refunds_settlement_attempts_nonnegative"
    )
    op.execute(
        "ALTER TABLE refunds "
        "DROP CONSTRAINT ck_refunds_settlement_status_valid"
    )
    op.drop_column('refunds', 'settled_at')
    op.drop_column('refunds', 'settlement_attempts')
    op.drop_column('refunds', 'settlement_failure_reason')
    op.drop_column('refunds', 'settlement_reference')
    op.drop_column('refunds', 'settlement_status')
