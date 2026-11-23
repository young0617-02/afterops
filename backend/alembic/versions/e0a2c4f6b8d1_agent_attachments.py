"""agent attachments

Revision ID: e0a2c4f6b8d1
Revises: d9f1b3c5e7a0
Create Date: 2026-11-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0a2c4f6b8d1'
down_revision: Union[str, Sequence[str], None] = 'd9f1b3c5e7a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'attachments',
        sa.Column('uploaded_by_user_id', sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        'fk_attachments_uploaded_by_user_id_users',
        'attachments',
        'users',
        ['uploaded_by_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'fk_attachments_uploaded_by_user_id_users',
        'attachments',
        type_='foreignkey',
    )
    op.drop_column('attachments', 'uploaded_by_user_id')
