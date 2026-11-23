"""quick replies

Revision ID: f7a9c1e3d5b8
Revises: e6f8c0d2b4a7
Create Date: 2026-11-05 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a9c1e3d5b8'
down_revision: Union[str, Sequence[str], None] = 'e6f8c0d2b4a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'quick_replies',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column(
            'category',
            sa.String(length=80),
            server_default='',
            nullable=False,
        ),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column(
            'status',
            sa.String(length=12),
            server_default='active',
            nullable=False,
        ),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('active','disabled')",
            name=op.f('ck_quick_replies_status_valid'),
        ),
        sa.ForeignKeyConstraint(
            ['merchant_id'],
            ['merchants.id'],
            name=op.f('fk_quick_replies_merchant_id_merchants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'],
            ['users.id'],
            name=op.f('fk_quick_replies_created_by_users'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_quick_replies')),
        sa.UniqueConstraint(
            'merchant_id',
            'title',
            name='uq_quick_replies_merchant_title',
        ),
    )
    op.create_index(
        'ix_quick_replies_merchant_status',
        'quick_replies',
        ['merchant_id', 'status'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_quick_replies_merchant_status', table_name='quick_replies'
    )
    op.drop_table('quick_replies')