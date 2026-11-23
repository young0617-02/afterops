"""after-sales charges

Revision ID: c3e5f7b9d1a2
Revises: b2d4f6a8c0e1
Create Date: 2026-11-10 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e5f7b9d1a2'
down_revision: Union[str, Sequence[str], None] = 'b2d4f6a8c0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'after_sales_charges',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('merchant_id', sa.BigInteger(), nullable=False),
        sa.Column('customer_id', sa.BigInteger(), nullable=False),
        sa.Column('order_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('return_request_id', sa.BigInteger(), nullable=True),
        sa.Column('repair_request_id', sa.BigInteger(), nullable=True),
        sa.Column('amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('reference', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('exchange_supplement','repair_fee')",
            name=op.f('ck_after_sales_charges_kind_valid'),
        ),
        sa.CheckConstraint(
            'amount_cents > 0',
            name=op.f('ck_after_sales_charges_amount_positive'),
        ),
        sa.ForeignKeyConstraint(
            ['merchant_id'],
            ['merchants.id'],
            name=op.f('fk_after_sales_charges_merchant_id_merchants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['customer_id'],
            ['customers.id'],
            name=op.f('fk_after_sales_charges_customer_id_customers'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['order_id'],
            ['orders.id'],
            name=op.f('fk_after_sales_charges_order_id_orders'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['return_request_id'],
            ['return_requests.id'],
            name=op.f('fk_after_sales_charges_return_request_id_return_requests'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['repair_request_id'],
            ['repair_requests.id'],
            name=op.f('fk_after_sales_charges_repair_request_id_repair_requests'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint(
            'id', name=op.f('pk_after_sales_charges')
        ),
        sa.UniqueConstraint(
            'reference',
            name=op.f('uq_after_sales_charges_reference'),
        ),
        sa.UniqueConstraint(
            'return_request_id',
            name=op.f('uq_after_sales_charges_return_request_id'),
        ),
        sa.UniqueConstraint(
            'repair_request_id',
            name=op.f('uq_after_sales_charges_repair_request_id'),
        ),
    )
    op.create_index(
        'ix_after_sales_charges_merchant_created',
        'after_sales_charges',
        ['merchant_id', 'created_at'],
        unique=False,
    )
    op.execute(
        "INSERT INTO after_sales_charges ("
        "merchant_id, customer_id, order_id, kind, return_request_id, "
        "amount_cents, reference, created_at) "
        "SELECT merchant_id, customer_id, order_id, 'exchange_supplement', "
        "id, exchange_supplement_cents, exchange_payment_ref, "
        "COALESCE(exchange_paid_at, now()) "
        "FROM return_requests "
        "WHERE exchange_payment_status = 'paid' "
        "AND exchange_supplement_cents > 0 "
        "AND exchange_payment_ref <> ''"
    )
    op.execute(
        "INSERT INTO after_sales_charges ("
        "merchant_id, customer_id, order_id, kind, repair_request_id, "
        "amount_cents, reference, created_at) "
        "SELECT merchant_id, customer_id, order_id, 'repair_fee', "
        "id, quote_cents, quote_payment_ref, "
        "COALESCE(quote_paid_at, now()) "
        "FROM repair_requests "
        "WHERE quote_status = 'paid' "
        "AND quote_cents > 0 "
        "AND quote_payment_ref <> ''"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_after_sales_charges_merchant_created',
        table_name='after_sales_charges',
    )
    op.drop_table('after_sales_charges')
