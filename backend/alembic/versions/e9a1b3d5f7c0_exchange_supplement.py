"""exchange supplement payments

Revision ID: e9a1b3d5f7c0
Revises: d8f0a2c4e6b9
Create Date: 2026-11-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9a1b3d5f7c0'
down_revision: Union[str, Sequence[str], None] = 'd8f0a2c4e6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_supplement_cents',
            sa.BigInteger(),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_payment_status',
            sa.String(length=16),
            server_default='none',
            nullable=False,
        ),
    )
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_payment_ref',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_paid_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "ALTER TABLE return_requests ADD CONSTRAINT "
        "ck_return_requests_exchange_supplement_nonnegative CHECK ("
        "exchange_supplement_cents >= 0)"
    )
    op.execute(
        "ALTER TABLE return_requests ADD CONSTRAINT "
        "ck_return_requests_exchange_payment_status_valid CHECK ("
        "exchange_payment_status IN ('none','pending','paid'))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE return_requests "
        "DROP CONSTRAINT ck_return_requests_exchange_payment_status_valid"
    )
    op.execute(
        "ALTER TABLE return_requests "
        "DROP CONSTRAINT ck_return_requests_exchange_supplement_nonnegative"
    )
    op.drop_column('return_requests', 'exchange_paid_at')
    op.drop_column('return_requests', 'exchange_payment_ref')
    op.drop_column('return_requests', 'exchange_payment_status')
    op.drop_column('return_requests', 'exchange_supplement_cents')
