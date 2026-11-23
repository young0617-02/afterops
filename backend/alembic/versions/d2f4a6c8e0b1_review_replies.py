"""review replies

Revision ID: d2f4a6c8e0b1
Revises: c1d3e5f7a9b2
Create Date: 2026-11-05 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f4a6c8e0b1'
down_revision: Union[str, Sequence[str], None] = 'c1d3e5f7a9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'product_reviews',
        sa.Column(
            'merchant_reply',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'product_reviews',
        sa.Column('replied_by', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'product_reviews',
        sa.Column(
            'replied_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        op.f('fk_product_reviews_replied_by_users'),
        'product_reviews',
        'users',
        ['replied_by'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('fk_product_reviews_replied_by_users'),
        'product_reviews',
        type_='foreignkey',
    )
    op.drop_column('product_reviews', 'replied_at')
    op.drop_column('product_reviews', 'replied_by')
    op.drop_column('product_reviews', 'merchant_reply')