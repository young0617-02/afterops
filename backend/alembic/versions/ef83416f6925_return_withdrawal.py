"""return withdrawal

Revision ID: ef83416f6925
Revises: 746d6c845172
Create Date: 2026-11-04 11:18:34.387012

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ef83416f6925'
down_revision: Union[str, Sequence[str], None] = '746d6c845172'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_return_requests_status_valid"),
        "return_requests",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_return_requests_status_valid"),
        "return_requests",
        "status IN ('requested','approved','in_transit','received','rejected','withdrawn')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE return_requests SET status='rejected' WHERE status='withdrawn'"
    )
    op.drop_constraint(
        op.f("ck_return_requests_status_valid"),
        "return_requests",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_return_requests_status_valid"),
        "return_requests",
        "status IN ('requested','approved','in_transit','received','rejected')",
    )
