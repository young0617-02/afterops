from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    Customer,
    Order,
    ReturnRequest,
)
from app.services.errors import NotFoundError


@dataclass(frozen=True)
class CustomerDirectoryRow:
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


@dataclass(frozen=True)
class CustomerOrderRow:
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    payable_cents: int
    placed_at: datetime


@dataclass(frozen=True)
class CustomerProposalRow:
    id: int
    action_type: str
    status: str
    amount_cents: int | None
    order_no: str | None
    created_at: datetime


@dataclass(frozen=True)
class CustomerReturnRow:
    id: int
    status: str
    resolution: str
    order_no: str
    created_at: datetime


@dataclass(frozen=True)
class CustomerDirectoryDetail:
    customer: CustomerDirectoryRow
    orders: tuple[CustomerOrderRow, ...]
    proposals: tuple[CustomerProposalRow, ...]
    returns: tuple[CustomerReturnRow, ...]


def _counts(
    session: Session, customer_ids: list[int]
) -> dict[int, dict[str, int | datetime | None]]:
    if not customer_ids:
        return {}
    counts: dict[int, dict[str, int | datetime | None]] = {
        customer_id: {
            "order_count": 0,
            "last_order_at": None,
            "after_sales_count": 0,
            "pending_proposals": 0,
            "active_returns": 0,
        }
        for customer_id in customer_ids
    }
    for customer_id, count, last_order in session.execute(
        select(
            Order.customer_id,
            func.count(Order.id),
            func.max(Order.placed_at),
        )
        .where(Order.customer_id.in_(customer_ids))
        .group_by(Order.customer_id)
    ).all():
        counts[customer_id]["order_count"] = int(count)
        counts[customer_id]["last_order_at"] = last_order
    for customer_id, count in session.execute(
        select(Order.customer_id, func.count(AfterSalesCase.id))
        .join(AfterSalesCase, AfterSalesCase.order_id == Order.id)
        .where(Order.customer_id.in_(customer_ids))
        .group_by(Order.customer_id)
    ).all():
        counts[customer_id]["after_sales_count"] = int(count)
    for customer_id, count in session.execute(
        select(Order.customer_id, func.count(ActionProposal.id))
        .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
        .join(Order, Order.id == AfterSalesCase.order_id)
        .where(
            Order.customer_id.in_(customer_ids),
            ActionProposal.status == "pending",
        )
        .group_by(Order.customer_id)
    ).all():
        counts[customer_id]["pending_proposals"] = int(count)
    for customer_id, count in session.execute(
        select(ReturnRequest.customer_id, func.count(ReturnRequest.id))
        .where(
            ReturnRequest.customer_id.in_(customer_ids),
            ReturnRequest.status.in_(("requested", "approved", "in_transit")),
        )
        .group_by(ReturnRequest.customer_id)
    ).all():
        counts[customer_id]["active_returns"] = int(count)
    return counts


def _row(
    customer: Customer, counts: dict[str, int | datetime | None]
) -> CustomerDirectoryRow:
    return CustomerDirectoryRow(
        id=customer.id,
        phone=customer.phone,
        name=customer.name,
        email=customer.email,
        created_at=customer.created_at,
        order_count=int(counts.get("order_count") or 0),
        last_order_at=counts.get("last_order_at"),
        after_sales_count=int(counts.get("after_sales_count") or 0),
        pending_proposals=int(counts.get("pending_proposals") or 0),
        active_returns=int(counts.get("active_returns") or 0),
    )


def list_customer_directory(
    session: Session,
    merchant_id: int,
    *,
    q: str | None = None,
    limit: int = 50,
) -> list[CustomerDirectoryRow]:
    stmt = select(Customer).where(Customer.merchant_id == merchant_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.phone.ilike(pattern),
                Customer.name.ilike(pattern),
                Customer.email.ilike(pattern),
            )
        )
    customers = session.execute(
        stmt.order_by(Customer.created_at.desc(), Customer.id.desc()).limit(limit)
    ).scalars().all()
    counts = _counts(session, [customer.id for customer in customers])
    return [_row(customer, counts.get(customer.id, {})) for customer in customers]


def get_customer_directory(
    session: Session, merchant_id: int, customer_id: int
) -> CustomerDirectoryDetail:
    customer = session.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.merchant_id == merchant_id,
        )
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError("客户不存在")
    counts = _counts(session, [customer.id]).get(customer.id, {})
    orders = session.execute(
        select(Order)
        .where(Order.customer_id == customer.id)
        .order_by(Order.placed_at.desc(), Order.id.desc())
        .limit(20)
    ).scalars().all()
    proposals = session.execute(
        select(ActionProposal, Order.order_no)
        .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
        .join(Order, Order.id == AfterSalesCase.order_id)
        .where(Order.customer_id == customer.id)
        .order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc())
        .limit(20)
    ).all()
    returns = session.execute(
        select(ReturnRequest, Order.order_no)
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(ReturnRequest.customer_id == customer.id)
        .order_by(ReturnRequest.created_at.desc(), ReturnRequest.id.desc())
        .limit(20)
    ).all()
    return CustomerDirectoryDetail(
        customer=_row(customer, counts),
        orders=tuple(
            CustomerOrderRow(
                order_no=order.order_no,
                status=order.status,
                payment_status=order.payment_status,
                fulfillment_status=order.fulfillment_status,
                payable_cents=order.payable_cents,
                placed_at=order.placed_at,
            )
            for order in orders
        ),
        proposals=tuple(
            CustomerProposalRow(
                id=proposal.id,
                action_type=proposal.action_type,
                status=proposal.status,
                amount_cents=(proposal.payload_json or {}).get("amount_cents"),
                order_no=order_no,
                created_at=proposal.created_at,
            )
            for proposal, order_no in proposals
        ),
        returns=tuple(
            CustomerReturnRow(
                id=request.id,
                status=request.status,
                resolution=request.resolution,
                order_no=order_no,
                created_at=request.created_at,
            )
            for request, order_no in returns
        ),
    )
