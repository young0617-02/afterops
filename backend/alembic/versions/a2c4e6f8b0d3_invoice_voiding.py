"""invoice voiding

Revision ID: a2c4e6f8b0d3
Revises: f1b3d5e7a9c2
Create Date: 2026-11-05 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2c4e6f8b0d3'
down_revision: Union[str, Sequence[str], None] = 'f1b3d5e7a9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        'uq_invoice_requests_order',
        'invoice_requests',
        type_='unique',
    )
    op.create_index(
        'ix_invoice_requests_order',
        'invoice_requests',
        ['order_id'],
    )
    op.drop_constraint(
        'status_valid', 'invoice_requests', type_='check'
    )
    op.create_check_constraint(
        'status_valid',
        'invoice_requests',
        "status IN ('requested','issued','rejected','voided')",
    )
    op.add_column(
        'invoice_requests',
        sa.Column(
            'void_reason',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'invoice_requests',
        sa.Column('voided_by', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'invoice_requests',
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_invoice_requests_voided_by_users',
        'invoice_requests',
        'users',
        ['voided_by'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'fk_invoice_requests_voided_by_users',
        'invoice_requests',
        type_='foreignkey',
    )
    op.drop_column('invoice_requests', 'voided_at')
    op.drop_column('invoice_requests', 'voided_by')
    op.drop_column('invoice_requests', 'void_reason')
    op.drop_constraint(
        'status_valid', 'invoice_requests', type_='check'
    )
    op.create_check_constraint(
        'status_valid',
        'invoice_requests',
        "status IN ('requested','issued','rejected')",
    )
    op.drop_index(
        'ix_invoice_requests_order', table_name='invoice_requests'
    )
    op.create_unique_constraint(
        'uq_invoice_requests_order',
        'invoice_requests',
        ['order_id'],
    )
