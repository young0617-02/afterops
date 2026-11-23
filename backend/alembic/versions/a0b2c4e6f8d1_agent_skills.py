"""agent handoff skills

Revision ID: a0b2c4e6f8d1
Revises: f9c1e3a5b7d2
Create Date: 2026-11-09 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a0b2c4e6f8d1'
down_revision: Union[str, Sequence[str], None] = 'f9c1e3a5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'handoff_skills',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'handoff_skills')