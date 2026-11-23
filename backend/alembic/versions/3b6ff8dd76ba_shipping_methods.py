"""shipping methods

Revision ID: 3b6ff8dd76ba
Revises: bda49e0f274e
Create Date: 2026-11-05 01:47:23.305388

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b6ff8dd76ba'
down_revision: Union[str, Sequence[str], None] = 'bda49e0f274e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "shipping_methods",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "fee_cents", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("free_over_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("fee_cents >= 0", name="fee_nonnegative"),
        sa.CheckConstraint(
            "free_over_cents IS NULL OR free_over_cents >= 0",
            name="free_over_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id", "code", name="uq_shipping_methods_merchant_code"
        ),
    )
    op.create_index(
        "ix_shipping_methods_merchant_status",
        "shipping_methods",
        ["merchant_id", "status", "sort_order"],
    )
    op.execute(
        """
        INSERT INTO shipping_methods
            (merchant_id, code, name, fee_cents, free_over_cents,
             status, sort_order)
        SELECT id, 'STANDARD', '标准配送', 0, NULL, 'active', 0
        FROM merchants
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_shipping_methods_merchant_status",
        table_name="shipping_methods",
    )
    op.drop_table("shipping_methods")
