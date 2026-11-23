"""return pickups

Revision ID: d3f5a7c9e1b4
Revises: c2e4f6a8b0d3
Create Date: 2026-11-10 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f5a7c9e1b4'
down_revision: Union[str, Sequence[str], None] = 'c2e4f6a8b0d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'return_pickups',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('return_request_id', sa.BigInteger(), nullable=False),
        sa.Column('customer_id', sa.BigInteger(), nullable=False),
        sa.Column('pickup_no', sa.String(length=32), nullable=False),
        sa.Column(
            'status',
            sa.String(length=16),
            server_default='scheduled',
            nullable=False,
        ),
        sa.Column('scheduled_date', sa.Date(), nullable=False),
        sa.Column('time_window', sa.String(length=16), nullable=False),
        sa.Column(
            'address_recipient',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'address_phone',
            sa.String(length=32),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'address_region',
            sa.String(length=32),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'address_city',
            sa.String(length=32),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'address_detail',
            sa.String(length=255),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'courier_name',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'courier_phone',
            sa.String(length=32),
            server_default='',
            nullable=False,
        ),
        sa.Column(
            'cancel_reason', sa.Text(), server_default='', nullable=False
        ),
        sa.Column(
            'picked_up_at', sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            'cancelled_at', sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['merchant_id'], ['merchants.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['return_request_id'],
            ['return_requests.id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['customer_id'], ['customers.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'merchant_id', 'pickup_no', name='uq_return_pickups_merchant_no'
        ),
        sa.CheckConstraint(
            "status IN ('scheduled','picked_up','cancelled')",
            name='ck_return_pickups_status_valid',
        ),
        sa.CheckConstraint(
            "time_window IN ('09:00-12:00','13:00-18:00','18:00-21:00')",
            name='ck_return_pickups_time_window_valid',
        ),
    )
    op.create_index(
        'ix_return_pickups_merchant_status_date',
        'return_pickups',
        ['merchant_id', 'status', 'scheduled_date'],
    )
    op.create_index(
        'ix_return_pickups_return',
        'return_pickups',
        ['return_request_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_return_pickups_return', table_name='return_pickups'
    )
    op.drop_index(
        'ix_return_pickups_merchant_status_date',
        table_name='return_pickups',
    )
    op.drop_table('return_pickups')
