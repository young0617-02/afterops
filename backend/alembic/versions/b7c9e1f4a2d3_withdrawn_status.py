"""withdrawn proposal status

Revision ID: b7c9e1f4a2d3
Revises: 79e9728ffb7e
Create Date: 2026-11-03 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b7c9e1f4a2d3"
down_revision: Union[str, Sequence[str], None] = "79e9728ffb7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_action_proposals_status_valid"),
        "action_proposals",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_action_proposals_status_valid"),
        "action_proposals",
        "status IN ('pending','approved','rejected','executing','executed','failed','expired','withdrawn')",
    )
    op.drop_constraint(
        op.f("ck_after_sales_cases_status_valid"),
        "after_sales_cases",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_after_sales_cases_status_valid"),
        "after_sales_cases",
        "status IN ('requested','reviewing','approved','rejected','executing','executed','failed','withdrawn')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE action_proposals SET status='rejected' WHERE status='withdrawn'"
    )
    op.execute(
        "UPDATE after_sales_cases SET status='rejected' WHERE status='withdrawn'"
    )
    op.drop_constraint(
        op.f("ck_action_proposals_status_valid"),
        "action_proposals",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_action_proposals_status_valid"),
        "action_proposals",
        "status IN ('pending','approved','rejected','executing','executed','failed','expired')",
    )
    op.drop_constraint(
        op.f("ck_after_sales_cases_status_valid"),
        "after_sales_cases",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_after_sales_cases_status_valid"),
        "after_sales_cases",
        "status IN ('requested','reviewing','approved','rejected','executing','executed','failed')",
    )
