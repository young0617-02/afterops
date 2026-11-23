"""repair parts cost

Revision ID: f6b8d0e2c4a6
Revises: e5a7c9d1b3f6
Create Date: 2026-11-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6b8d0e2c4a6'
down_revision: Union[str, Sequence[str], None] = 'e5a7c9d1b3f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'repair_requests',
        sa.Column(
            'parts_cost_cents',
            sa.BigInteger(),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'parts_note',
            sa.Text(),
            server_default='',
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE repair_requests ADD CONSTRAINT "
        "ck_repair_requests_parts_cost_nonnegative CHECK ("
        "parts_cost_cents >= 0)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE repair_requests "
        "DROP CONSTRAINT ck_repair_requests_parts_cost_nonnegative"
    )
    op.drop_column('repair_requests', 'parts_note')
    op.drop_column('repair_requests', 'parts_cost_cents')
