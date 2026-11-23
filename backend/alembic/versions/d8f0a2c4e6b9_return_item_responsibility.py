"""return item responsibility

Revision ID: d8f0a2c4e6b9
Revises: c7e9a1b3d5f8
Create Date: 2026-11-10 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f0a2c4e6b9'
down_revision: Union[str, Sequence[str], None] = 'c7e9a1b3d5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_items',
        sa.Column('responsibility', sa.String(length=16), nullable=True),
    )
    op.execute(
        "ALTER TABLE return_items ADD CONSTRAINT "
        "ck_return_items_responsibility_valid CHECK ("
        "responsibility IS NULL OR responsibility IN "
        "('merchant','customer','logistics','other'))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE return_items "
        "DROP CONSTRAINT ck_return_items_responsibility_valid"
    )
    op.drop_column('return_items', 'responsibility')
