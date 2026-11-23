"""review follow up

Revision ID: c9e1b3d5f7a0
Revises: b8d0f2a4c6e9
Create Date: 2026-11-06 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e1b3d5f7a0'
down_revision: Union[str, Sequence[str], None] = 'b8d0f2a4c6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'product_reviews',
        sa.Column(
            'follow_up_content',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'product_reviews',
        sa.Column('follow_up_attachment_ids', sa.JSON(), nullable=True),
    )
    op.add_column(
        'product_reviews',
        sa.Column(
            'followed_up_at', sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('product_reviews', 'followed_up_at')
    op.drop_column('product_reviews', 'follow_up_attachment_ids')
    op.drop_column('product_reviews', 'follow_up_content')
