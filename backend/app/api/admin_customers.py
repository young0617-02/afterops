from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.admin import AdminDep
from app.api.deps import SessionDep
from app.domain.models import Customer
from app.domain.money import format_cents
from app.services.after_sales_timeline import (
    customer_after_sales_timeline,
)
from app.services.customer_directory import (
    get_customer_directory,
    list_customer_directory,
)
from app.services.customer_risk import customer_after_sales_risk
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CustomerDirectoryOut(BaseModel):
    id: int
    phone: str | None
    name: str | None
    email: str | None
    created_at: datetime
    order_count: int
    last_order_at: datetime | None
    after_sales_count: int
    pending_proposals: int
    active_returns: int


class CustomerOrderOut(BaseModel):
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    payable_cents: int
    payable_display: str
    placed_at: datetime


class CustomerProposalOut(BaseModel):
    id: int
    action_type: str
    status: str
    amount_cents: int | None
    amount_display: str | None
    order_no: str | None
    created_at: datetime


class CustomerReturnOut(BaseModel):
    id: int
    status: str
    resolution: str
    order_no: str
    created_at: datetime


class CustomerRiskOut(BaseModel):
    level: str
    reasons: list[str]
    window_days: int
    order_count: int
    return_count: int
    return_rate: float
    cancelled_exchange_count: int
    appeal_count: int
    customer_responsibility_count: int
    last_return_at: datetime | None


class CustomerDetailOut(CustomerDirectoryOut):
    orders: list[CustomerOrderOut]
    proposals: list[CustomerProposalOut]
    returns: list[CustomerReturnOut]
    after_sales_risk: CustomerRiskOut


def _summary(row) -> CustomerDirectoryOut:
    return CustomerDirectoryOut(
        id=row.id,
        phone=row.phone,
        name=row.name,
        email=row.email,
        created_at=row.created_at,
        order_count=row.order_count,
        last_order_at=row.last_order_at,
        after_sales_count=row.after_sales_count,
        pending_proposals=row.pending_proposals,
        active_returns=row.active_returns,
    )


@router.get("/customers", response_model=list[CustomerDirectoryOut])
def admin_customers(
    admin: AdminDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[CustomerDirectoryOut]:
    return [
        _summary(row)
        for row in list_customer_directory(
            session, admin.merchant_id, q=q, limit=limit
        )
    ]


@router.get("/customers/{customer_id}", response_model=CustomerDetailOut)
def admin_customer_detail(
    customer_id: int,
    admin: AdminDep,
    session: SessionDep,
) -> CustomerDetailOut:
    detail = get_customer_directory(session, admin.merchant_id, customer_id)
    risk = customer_after_sales_risk(
        session, admin.merchant_id, customer_id
    )
    return CustomerDetailOut(
        **_summary(detail.customer).model_dump(),
        orders=[
            CustomerOrderOut(
                order_no=order.order_no,
                status=order.status,
                payment_status=order.payment_status,
                fulfillment_status=order.fulfillment_status,
                payable_cents=order.payable_cents,
                payable_display=format_cents(order.payable_cents),
                placed_at=order.placed_at,
            )
            for order in detail.orders
        ],
        proposals=[
            CustomerProposalOut(
                id=proposal.id,
                action_type=proposal.action_type,
                status=proposal.status,
                amount_cents=proposal.amount_cents,
                amount_display=(
                    format_cents(proposal.amount_cents)
                    if proposal.amount_cents is not None
                    else None
                ),
                order_no=proposal.order_no,
                created_at=proposal.created_at,
            )
            for proposal in detail.proposals
        ],
        returns=[
            CustomerReturnOut(
                id=request.id,
                status=request.status,
                resolution=request.resolution,
                order_no=request.order_no,
                created_at=request.created_at,
            )
            for request in detail.returns
        ],
        after_sales_risk=CustomerRiskOut(
            level=risk.level,
            reasons=list(risk.reasons),
            window_days=risk.window_days,
            order_count=risk.order_count,
            return_count=risk.return_count,
            return_rate=risk.return_rate,
            cancelled_exchange_count=risk.cancelled_exchange_count,
            appeal_count=risk.appeal_count,
            customer_responsibility_count=(
                risk.customer_responsibility_count
            ),
            last_return_at=risk.last_return_at,
        ),
    )


class CustomerTimelineEventOut(BaseModel):
    kind: str
    occurred_at: datetime
    title: str
    detail: str
    status: str
    order_no: str | None
    entity_id: int | None


@router.get(
    "/customers/{customer_id}/timeline",
    response_model=list[CustomerTimelineEventOut],
)
def admin_customer_timeline(
    customer_id: int,
    admin: AdminDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CustomerTimelineEventOut]:
    customer = session.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.merchant_id == admin.merchant_id,
        )
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError("客户不存在")
    return [
        CustomerTimelineEventOut(
            kind=event.kind,
            occurred_at=event.occurred_at,
            title=event.title,
            detail=event.detail,
            status=event.status,
            order_no=event.order_no,
            entity_id=event.entity_id,
        )
        for event in customer_after_sales_timeline(
            session, customer, limit=limit
        )
    ]
