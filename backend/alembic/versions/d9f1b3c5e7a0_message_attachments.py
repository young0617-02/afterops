"""message attachments

Revision ID: d9f1b3c5e7a0
Revises: c8e0f2a4b6d8
Create Date: 2026-11-05 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9f1b3c5e7a0'
down_revision: Union[str, Sequence[str], None] = 'c8e0f2a4b6d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'messages',
        sa.Column('attachment_ids', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'attachment_ids')
