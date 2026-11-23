"""return inspection

Revision ID: f1b3d5e7a9c2
Revises: e0a2c4f6b8d1
Create Date: 2026-11-05 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b3d5e7a9c2'
down_revision: Union[str, Sequence[str], None] = 'e0a2c4f6b8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_items',
        sa.Column('disposition', sa.String(length=12), nullable=True),
    )
    op.add_column(
        'return_items',
        sa.Column('inspected_by', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'return_items',
        sa.Column('inspected_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_return_items_inspected_by_users',
        'return_items',
        'users',
        ['inspected_by'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_check_constraint(
        'disposition_valid',
        'return_items',
        "disposition IS NULL OR disposition IN ('restock','damaged')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'disposition_valid', 'return_items', type_='check'
    )
    op.drop_constraint(
        'fk_return_items_inspected_by_users',
        'return_items',
        type_='foreignkey',
    )
    op.drop_column('return_items', 'inspected_at')
    op.drop_column('return_items', 'inspected_by')
    op.drop_column('return_items', 'disposition')
