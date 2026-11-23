"""return category windows

Revision ID: b9c1e3d5f7a0
Revises: a8b0d2f4e6c9
Create Date: 2026-11-05 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b9c1e3d5f7a0'
down_revision: Union[str, Sequence[str], None] = 'a8b0d2f4e6c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_policies',
        sa.Column(
            'category_windows',
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), 'postgresql'
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('return_policies', 'category_windows')