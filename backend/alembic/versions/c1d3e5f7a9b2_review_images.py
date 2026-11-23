"""review images

Revision ID: c1d3e5f7a9b2
Revises: b9c1e3d5f7a0
Create Date: 2026-11-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1d3e5f7a9b2'
down_revision: Union[str, Sequence[str], None] = 'b9c1e3d5f7a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'product_reviews',
        sa.Column(
            'attachment_ids',
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), 'postgresql'
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('product_reviews', 'attachment_ids')