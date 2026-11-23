"""repair return shipping

Revision ID: d6f8b0c2e4a7
Revises: c5e7a9b1d3f6
Create Date: 2026-11-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6f8b0c2e4a7'
down_revision: Union[str, Sequence[str], None] = 'c5e7a9b1d3f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE repair_requests "
        "DROP CONSTRAINT ck_repair_requests_status_valid"
    )
    op.execute(
        "ALTER TABLE repair_requests ADD CONSTRAINT "
        "ck_repair_requests_status_valid CHECK (status IN "
        "('requested','approved','rejected','in_transit','received',"
        "'return_shipped','completed','withdrawn'))"
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'return_carrier',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'return_tracking_no',
            sa.String(length=64),
            server_default='',
            nullable=False,
        ),
    )
    op.add_column(
        'repair_requests',
        sa.Column(
            'repaired_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('repair_requests', 'repaired_at')
    op.drop_column('repair_requests', 'return_tracking_no')
    op.drop_column('repair_requests', 'return_carrier')
    op.execute(
        "ALTER TABLE repair_requests "
        "DROP CONSTRAINT ck_repair_requests_status_valid"
    )
    op.execute(
        "ALTER TABLE repair_requests ADD CONSTRAINT "
        "ck_repair_requests_status_valid CHECK (status IN "
        "('requested','approved','rejected','in_transit','received',"
        "'completed','withdrawn'))"
    )
