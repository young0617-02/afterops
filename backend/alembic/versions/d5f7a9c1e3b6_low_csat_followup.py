"""low csat follow up

Revision ID: d5f7a9c1e3b6
Revises: c4e6f8a0d2b5
Create Date: 2026-11-05 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f7a9c1e3b6'
down_revision: Union[str, Sequence[str], None] = 'c4e6f8a0d2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'handoff_tickets',
        sa.Column('follow_up_for_ticket_id', sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        'fk_handoff_tickets_follow_up_for_ticket_id_handoff_tickets',
        'handoff_tickets',
        'handoff_tickets',
        ['follow_up_for_ticket_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'fk_handoff_tickets_follow_up_for_ticket_id_handoff_tickets',
        'handoff_tickets',
        type_='foreignkey',
    )
    op.drop_column('handoff_tickets', 'follow_up_for_ticket_id')
