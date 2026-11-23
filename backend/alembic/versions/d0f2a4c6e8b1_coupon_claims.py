"""coupon claims

Revision ID: d0f2a4c6e8b1
Revises: c9e1b3d5f7a0
Create Date: 2026-11-06 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0f2a4c6e8b1'
down_revision: Union[str, Sequence[str], None] = 'c9e1b3d5f7a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'coupons',
        sa.Column(
            'claimable',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
    )
    op.create_table(
        'coupon_claims',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('coupon_id', sa.BigInteger(), nullable=False),
        sa.Column('customer_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'claimed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['coupon_id'], ['coupons.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['customer_id'], ['customers.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['merchant_id'], ['merchants.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'coupon_id',
            'customer_id',
            name='uq_coupon_claims_coupon_customer',
        ),
    )
    op.create_index(
        'ix_coupon_claims_customer_created',
        'coupon_claims',
        ['customer_id', 'claimed_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_coupon_claims_customer_created',
        table_name='coupon_claims',
    )
    op.drop_table('coupon_claims')
    op.drop_column('coupons', 'claimable')
