"""handoff notes

Revision ID: b6d8f0a2c4e6
Revises: a5c7e9b1d3f5
Create Date: 2026-11-05 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6d8f0a2c4e6'
down_revision: Union[str, Sequence[str], None] = 'a5c7e9b1d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'handoff_notes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('ticket_id', sa.BigInteger(), nullable=False),
        sa.Column('author_user_id', sa.BigInteger(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['merchant_id'],
            ['merchants.id'],
            name=op.f('fk_handoff_notes_merchant_id_merchants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['ticket_id'],
            ['handoff_tickets.id'],
            name=op.f('fk_handoff_notes_ticket_id_handoff_tickets'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['author_user_id'],
            ['users.id'],
            name=op.f('fk_handoff_notes_author_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_handoff_notes')),
    )
    op.create_index(
        'ix_handoff_notes_ticket_created',
        'handoff_notes',
        ['ticket_id', 'created_at'],
        unique=False,
    )
    op.create_index(
        'ix_handoff_notes_merchant',
        'handoff_notes',
        ['merchant_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_handoff_notes_merchant', table_name='handoff_notes'
    )
    op.drop_index(
        'ix_handoff_notes_ticket_created', table_name='handoff_notes'
    )
    op.drop_table('handoff_notes')