"""return reason category

Revision ID: e3a5b7c9d1f0
Revises: d2f4a6c8e0b1
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3a5b7c9d1f0'
down_revision: Union[str, Sequence[str], None] = 'd2f4a6c8e0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'return_requests',
        sa.Column(
            'reason_category',
            sa.String(length=20),
            server_default='other',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_return_requests_reason_category_valid'),
        'return_requests',
        "reason_category IN ('quality','damaged','wrong_item','size',"
        "'not_needed','other')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_return_requests_reason_category_valid'),
        'return_requests',
        type_='check',
    )
    op.drop_column('return_requests', 'reason_category')