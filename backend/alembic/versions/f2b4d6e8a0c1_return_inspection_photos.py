"""return inspection photos

Revision ID: f2b4d6e8a0c1
Revises: e1a3c5d7f9b2
Create Date: 2026-11-06 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b4d6e8a0c1'
down_revision: Union[str, Sequence[str], None] = 'e1a3c5d7f9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column('inspection_attachment_ids', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('return_requests', 'inspection_attachment_ids')
