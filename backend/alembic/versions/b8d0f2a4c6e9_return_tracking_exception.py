"""return tracking exception

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1b3d5f8
Create Date: 2026-11-06 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b8d0f2a4c6e9'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1b3d5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        'status_valid', 'return_tracking_events', type_='check'
    )
    op.create_check_constraint(
        'status_valid',
        'return_tracking_events',
        "status IN ('registered','picked_up','in_transit',"
        "'out_for_delivery','delivered','exception')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'status_valid', 'return_tracking_events', type_='check'
    )
    op.create_check_constraint(
        'status_valid',
        'return_tracking_events',
        "status IN ('registered','picked_up','in_transit',"
        "'out_for_delivery','delivered')",
    )
