"""repair quotes

Revision ID: f5a7c9e1b3d5
Revises: e4f6a8c0d2b5
Create Date: 2026-11-10 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5a7c9e1b3d5'
down_revision: Union[str, Sequence[str], None] = 'e4f6a8c0d2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'repair_requests',
        sa.Column('quote_cents', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_status',
            sa.String(length=12),
            server_default='none',
            nullable=False,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_note', sa.Text(), server_default='', nullable=False
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_created_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_responded_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_paid_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_payment_ref',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE repair_requests ADD CONSTRAINT "
        "ck_repair_requests_quote_status_valid CHECK (quote_status IN "
        "('none','pending','accepted','declined','paid'))"
    )
    op.execute(
        "ALTER TABLE repair_requests ADD CONSTRAINT "
        "ck_repair_requests_quote_nonnegative CHECK ("
        "quote_cents IS NULL OR quote_cents >= 0)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE repair_requests "
        "DROP CONSTRAINT ck_repair_requests_quote_nonnegative"
    )
    op.execute(
        "ALTER TABLE repair_requests "
        "DROP CONSTRAINT ck_repair_requests_quote_status_valid"
    )
    op.drop_column('repair_requests', 'quote_payment_ref')
    op.drop_column('repair_requests', 'quote_paid_at')
    op.drop_column('repair_requests', 'quote_responded_at')
    op.drop_column('repair_requests', 'quote_created_at')
    op.drop_column('repair_requests', 'quote_note')
    op.drop_column('repair_requests', 'quote_status')
    op.drop_column('repair_requests', 'quote_cents')
