"""agent messages

Revision ID: c7d2a4e91b30
Revises: fe0ea55b3627
Create Date: 2026-11-05 04:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7d2a4e91b30'
down_revision: Union[str, Sequence[str], None] = 'fe0ea55b3627'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f('ck_messages_role_valid'), 'messages', type_='check'
    )
    op.create_check_constraint(
        op.f('ck_messages_role_valid'),
        'messages',
        "role IN ('user','assistant','tool','system','agent')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_messages_role_valid'), 'messages', type_='check'
    )
    op.create_check_constraint(
        op.f('ck_messages_role_valid'),
        'messages',
        "role IN ('user','assistant','tool','system')",
    )