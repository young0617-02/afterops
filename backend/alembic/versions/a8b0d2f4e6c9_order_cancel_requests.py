"""order cancel requests

Revision ID: a8b0d2f4e6c9
Revises: f7a9c1e3d5b8
Create Date: 2026-11-05 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b0d2f4e6c9'
down_revision: Union[str, Sequence[str], None] = 'f7a9c1e3d5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'orders',
        sa.Column(
            'cancel_requested_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'cancel_requested_at')