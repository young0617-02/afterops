"""repair requests

Revision ID: b4fda5c9b198
Revises: 7036da747308
Create Date: 2026-11-04 23:47:37.477252

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4fda5c9b198'
down_revision: Union[str, Sequence[str], None] = '7036da747308'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "repair_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="requested",
            nullable=False,
        ),
        sa.Column("issue", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "attachment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "carrier", sa.String(length=64), server_default="", nullable=False
        ),
        sa.Column(
            "tracking_no",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "rejected_reason", sa.Text(), server_default="", nullable=False
        ),
        sa.Column(
            "resolution_note", sa.Text(), server_default="", nullable=False
        ),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "approved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
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
            "status IN ('requested','approved','rejected','in_transit',"
            "'received','completed','withdrawn')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], ondelete="SET NULL"
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
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repair_requests_merchant_status",
        "repair_requests",
        ["merchant_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_repair_requests_order_item",
        "repair_requests",
        ["order_item_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_repair_requests_order_item", table_name="repair_requests"
    )
    op.drop_index(
        "ix_repair_requests_merchant_status", table_name="repair_requests"
    )
    op.drop_table("repair_requests")
