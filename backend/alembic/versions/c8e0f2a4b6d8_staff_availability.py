"""staff availability

Revision ID: c8e0f2a4b6d8
Revises: b6d8f0a2c4e6
Create Date: 2026-11-05 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e0f2a4b6d8'
down_revision: Union[str, Sequence[str], None] = 'b6d8f0a2c4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'handoff_available',
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'handoff_available')