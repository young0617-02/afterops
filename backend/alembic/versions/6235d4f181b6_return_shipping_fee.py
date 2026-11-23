"""return shipping fee

Revision ID: 6235d4f181b6
Revises: 4d66f438a712
Create Date: 2026-11-04 22:18:48.187877

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6235d4f181b6'
down_revision: Union[str, Sequence[str], None] = '4d66f438a712'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "return_policies",
        sa.Column(
            "return_shipping_fee_cents",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "return_shipping_fee_range",
        "return_policies",
        "return_shipping_fee_cents >= 0 AND return_shipping_fee_cents <= 20000",
    )
    op.add_column(
        "return_requests",
        sa.Column("refund_amount_cents", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("return_requests", "refund_amount_cents")
    op.drop_constraint(
        "ck_return_policies_return_shipping_fee_range",
        "return_policies",
        type_="check",
    )
    op.drop_column("return_policies", "return_shipping_fee_cents")
