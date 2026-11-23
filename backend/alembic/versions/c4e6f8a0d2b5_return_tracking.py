"""return tracking

Revision ID: c4e6f8a0d2b5
Revises: b3d5f7a9c1e4
Create Date: 2026-11-05 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e6f8a0d2b5'
down_revision: Union[str, Sequence[str], None] = 'b3d5f7a9c1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column(
            'shipment_status',
            sa.String(length=20),
            server_default='none',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        'shipment_status_valid',
        'return_requests',
        "shipment_status IN ('none','registered','picked_up','in_transit',"
        "'out_for_delivery','delivered')",
    )
    op.create_table(
        'return_tracking_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('return_request_id', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), server_default='', nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'happened_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['return_request_id'], ['return_requests.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('registered','picked_up','in_transit',"
            "'out_for_delivery','delivered')",
            name='status_valid',
        ),
    )
    op.create_index(
        'ix_return_tracking_events_request',
        'return_tracking_events',
        ['return_request_id', 'happened_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_return_tracking_events_request',
        table_name='return_tracking_events',
    )
    op.drop_table('return_tracking_events')
    op.drop_constraint(
        'shipment_status_valid',
        'return_requests',
        type_='check',
    )
    op.drop_column('return_requests', 'shipment_status')
