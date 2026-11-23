"""exchange cancellation

Revision ID: a1c3e5f7b9d2
Revises: f0a2c4e6b8d1
Create Date: 2026-11-10 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3e5f7b9d2'
down_revision: Union[str, Sequence[str], None] = 'f0a2c4e6b8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_cancelled_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_cancel_reason',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'return_requests',
        sa.Column(
            'exchange_cancelled_by',
            sa.String(length=16),
            server_default='',
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE return_requests "
        "DROP CONSTRAINT ck_return_requests_status_valid"
    )
    op.execute(
        "ALTER TABLE return_requests ADD CONSTRAINT "
        "ck_return_requests_status_valid CHECK ("
        "status IN ('requested','approved','in_transit','received',"
        "'rejected','withdrawn','exchange_cancelled'))"
    )
    op.execute(
        "ALTER TABLE return_requests "
        "DROP CONSTRAINT ck_return_requests_exchange_payment_status_valid"
    )
    op.execute(
        "ALTER TABLE return_requests ADD CONSTRAINT "
        "ck_return_requests_exchange_payment_status_valid CHECK ("
        "exchange_payment_status IN ('none','pending','paid','cancelled'))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE return_requests "
        "DROP CONSTRAINT ck_return_requests_exchange_payment_status_valid"
    )
    op.execute(
        "ALTER TABLE return_requests ADD CONSTRAINT "
        "ck_return_requests_exchange_payment_status_valid CHECK ("
        "exchange_payment_status IN ('none','pending','paid'))"
    )
    op.execute(
        "ALTER TABLE return_requests "
        "DROP CONSTRAINT ck_return_requests_status_valid"
    )
    op.execute(
        "ALTER TABLE return_requests ADD CONSTRAINT "
        "ck_return_requests_status_valid CHECK ("
        "status IN ('requested','approved','in_transit','received',"
        "'rejected','withdrawn'))"
    )
    op.drop_column('return_requests', 'exchange_cancelled_by')
    op.drop_column('return_requests', 'exchange_cancel_reason')
    op.drop_column('return_requests', 'exchange_cancelled_at')
