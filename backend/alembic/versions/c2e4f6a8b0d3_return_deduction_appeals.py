"""return deduction appeals

Revision ID: c2e4f6a8b0d3
Revises: b1d3f5a7c9e2
Create Date: 2026-11-09 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c2e4f6a8b0d3'
down_revision: Union[str, Sequence[str], None] = 'b1d3f5a7c9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'return_deduction_appeals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('return_request_id', sa.BigInteger(), nullable=False),
        sa.Column('customer_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'status', sa.String(length=16), server_default='pending', nullable=False
        ),
        sa.Column('reason', sa.Text(), server_default='', nullable=False),
        sa.Column(
            'attachment_ids',
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), 'postgresql'
            ),
            nullable=True,
        ),
        sa.Column('requested_cents', sa.BigInteger(), nullable=False),
        sa.Column('approved_cents', sa.BigInteger(), nullable=True),
        sa.Column(
            'resolution_note', sa.Text(), server_default='', nullable=False
        ),
        sa.Column('resolved_by', sa.BigInteger(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
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
            ['return_request_id'], ['return_requests.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['customer_id'], ['customers.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['resolved_by'], ['users.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'return_request_id', name='uq_return_deduction_appeals_return'
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','withdrawn')",
            name='ck_return_deduction_appeals_status_valid',
        ),
        sa.CheckConstraint(
            'requested_cents >= 0',
            name='ck_return_deduction_appeals_requested_nonnegative',
        ),
        sa.CheckConstraint(
            'approved_cents IS NULL OR approved_cents >= 0',
            name='ck_return_deduction_appeals_approved_nonnegative',
        ),
    )
    op.create_index(
        'ix_return_deduction_appeals_merchant_status',
        'return_deduction_appeals',
        ['merchant_id', 'status', 'created_at'],
    )
    op.create_index(
        'ix_return_deduction_appeals_customer',
        'return_deduction_appeals',
        ['customer_id', 'created_at'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_return_deduction_appeals_customer',
        table_name='return_deduction_appeals',
    )
    op.drop_index(
        'ix_return_deduction_appeals_merchant_status',
        table_name='return_deduction_appeals',
    )
    op.drop_table('return_deduction_appeals')
