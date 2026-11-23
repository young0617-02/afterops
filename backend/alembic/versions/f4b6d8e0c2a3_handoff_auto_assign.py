"""handoff auto assign

Revision ID: f4b6d8e0c2a3
Revises: e3a5b7c9d1f0
Create Date: 2026-11-05 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b6d8e0c2a3'
down_revision: Union[str, Sequence[str], None] = 'e3a5b7c9d1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'merchants',
        sa.Column(
            'auto_assign_handoffs',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('merchants', 'auto_assign_handoffs')