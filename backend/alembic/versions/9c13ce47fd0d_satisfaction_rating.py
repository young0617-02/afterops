"""satisfaction rating

Revision ID: 9c13ce47fd0d
Revises: 6235d4f181b6
Create Date: 2026-11-04 22:47:46.861325

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c13ce47fd0d'
down_revision: Union[str, Sequence[str], None] = '6235d4f181b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "satisfaction_ratings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("handoff_ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "score >= 1 AND score <= 5", name="score_range"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["handoff_ticket_id"],
            ["handoff_tickets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "handoff_ticket_id", name="uq_satisfaction_ratings_ticket"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("satisfaction_ratings")
