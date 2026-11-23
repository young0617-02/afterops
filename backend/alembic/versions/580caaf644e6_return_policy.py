"""return policy

Revision ID: 580caaf644e6
Revises: 962c3bf311fd
Create Date: 2026-11-04 21:27:02.646050

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '580caaf644e6'
down_revision: Union[str, Sequence[str], None] = '962c3bf311fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "return_policies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("window_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column(
            "allow_exchange",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "final_sale_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "window_days >= 0 AND window_days <= 90",
            name="window_days_range",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", name="uq_return_policies_merchant"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("return_policies")
