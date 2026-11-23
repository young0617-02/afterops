"""handoff summaries

Revision ID: e4f6a8c0d2b5
Revises: d3f5a7c9e1b4
Create Date: 2026-11-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4f6a8c0d2b5'
down_revision: Union[str, Sequence[str], None] = 'd3f5a7c9e1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'handoff_tickets',
        sa.Column('summary', sa.Text(), server_default='', nullable=False),
    )
    op.add_column(
        'handoff_tickets',
        sa.Column(
            'summary_generated_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('handoff_tickets', 'summary_generated_at')
    op.drop_column('handoff_tickets', 'summary')
