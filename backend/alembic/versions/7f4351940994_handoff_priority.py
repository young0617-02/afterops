"""handoff priority

Revision ID: 7f4351940994
Revises: ef83416f6925
Create Date: 2026-11-04 15:18:36.746601

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f4351940994'
down_revision: Union[str, Sequence[str], None] = 'ef83416f6925'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "handoff_tickets",
        sa.Column(
            "priority",
            sa.String(length=8),
            server_default="normal",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_handoff_tickets_priority_valid"),
        "handoff_tickets",
        "priority IN ('normal','high','urgent')",
    )
    op.create_index(
        "ix_handoff_tickets_merchant_priority",
        "handoff_tickets",
        ["merchant_id", "status", "priority", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_handoff_tickets_merchant_priority",
        table_name="handoff_tickets",
    )
    op.drop_constraint(
        op.f("ck_handoff_tickets_priority_valid"),
        "handoff_tickets",
        type_="check",
    )
    op.drop_column("handoff_tickets", "priority")
