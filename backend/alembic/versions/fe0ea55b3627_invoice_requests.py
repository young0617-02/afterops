"""invoice requests

Revision ID: fe0ea55b3627
Revises: 3b6ff8dd76ba
Create Date: 2026-11-05 02:47:14.734885

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe0ea55b3627'
down_revision: Union[str, Sequence[str], None] = '3b6ff8dd76ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "invoice_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("invoice_type", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "tax_no", sa.String(length=32), server_default="", nullable=False
        ),
        sa.Column(
            "email",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="requested",
            nullable=False,
        ),
        sa.Column(
            "invoice_no",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "reject_reason", sa.Text(), server_default="", nullable=False
        ),
        sa.Column("issued_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), nullable=True
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
            "invoice_type IN ('personal','company')",
            name="invoice_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('requested','issued','rejected')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "amount_cents >= 0", name="amount_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["issued_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_invoice_requests_order"),
    )
    op.create_index(
        "ix_invoice_requests_merchant_status",
        "invoice_requests",
        ["merchant_id", "status", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_invoice_requests_merchant_status",
        table_name="invoice_requests",
    )
    op.drop_table("invoice_requests")
