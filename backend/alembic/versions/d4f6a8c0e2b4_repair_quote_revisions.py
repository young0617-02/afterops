"""repair quote revisions

Revision ID: d4f6a8c0e2b4
Revises: c3e5f7b9d1a2
Create Date: 2026-11-12 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f6a8c0e2b4'
down_revision: Union[str, Sequence[str], None] = 'c3e5f7b9d1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'repair_requests',
        sa.Column(
            'quote_revised_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('repair_requests', 'quote_revised_at')
