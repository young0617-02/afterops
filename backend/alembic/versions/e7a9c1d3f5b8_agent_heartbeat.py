"""agent heartbeat

Revision ID: e7a9c1d3f5b8
Revises: d6f8b0c2e4a7
Create Date: 2026-11-09 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a9c1d3f5b8'
down_revision: Union[str, Sequence[str], None] = 'd6f8b0c2e4a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'last_seen_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'last_seen_at')
