"""coupons

Revision ID: bda49e0f274e
Revises: 571815bce88b
Create Date: 2026-11-05 00:47:39.753899

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bda49e0f274e'
down_revision: Union[str, Sequence[str], None] = '571815bce88b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "coupons",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("discount_type", sa.String(length=10), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column(
            "min_spend_cents",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column(
            "used_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "per_customer_limit",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="active",
            nullable=False,
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
        sa.CheckConstraint(
            "discount_type IN ('fixed','percent')",
            name="discount_type_valid",
        ),
        sa.CheckConstraint("value > 0", name="value_positive"),
        sa.CheckConstraint(
            "min_spend_cents >= 0", name="min_spend_nonnegative"
        ),
        sa.CheckConstraint(
            "used_count >= 0", name="used_count_nonnegative"
        ),
        sa.CheckConstraint(
            "per_customer_limit >= 1", name="per_customer_limit_positive"
        ),
        sa.CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on",
            name="validity_order",
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
            "merchant_id", "code", name="uq_coupons_merchant_code"
        ),
    )
    op.create_index(
        "ix_coupons_merchant_status", "coupons", ["merchant_id", "status"]
    )
    op.create_table(
        "coupon_redemptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("coupon_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("discount_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["coupon_id"], ["coupons.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", name="uq_coupon_redemptions_order"
        ),
    )
    op.create_index(
        "ix_coupon_redemptions_coupon_customer",
        "coupon_redemptions",
        ["coupon_id", "customer_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_coupon_redemptions_coupon_customer",
        table_name="coupon_redemptions",
    )
    op.drop_table("coupon_redemptions")
    op.drop_index("ix_coupons_merchant_status", table_name="coupons")
    op.drop_table("coupons")
