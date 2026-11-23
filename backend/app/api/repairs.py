from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import CurrentCustomer, SessionDep
from app.services.repairs import (
    RepairView,
    accept_repair_quote,
    add_repair_tracking,
    approve_repair,
    complete_repair,
    confirm_repair_received,
    create_repair,
    decline_repair_quote,
    list_repairs_for_admin,
    list_repairs_for_customer,
    pay_repair_quote,
    quote_repair,
    receive_repair,
    reject_repair,
    repair_eligibility,
    withdraw_repair,
)

router = APIRouter(tags=["repairs"])


class RepairOut(BaseModel):
    id: int
    order_no: str
    sku: str
    title: str
    status: str
    issue: str
    attachment_ids: list[int]
    carrier: str
    tracking_no: str
    return_carrier: str
    return_tracking_no: str
    return_address: str
    rejected_reason: str
    resolution_note: str
    quote_cents: int | None
    quote_status: str
    quote_note: str
    quote_created_at: datetime | None
    quote_revised_at: datetime | None
    quote_responded_at: datetime | None
    quote_paid_at: datetime | None
    quote_payment_ref: str
    parts_cost_cents: int | None
    parts_note: str | None
    created_at: datetime
    approved_at: datetime | None
    received_at: datetime | None
    repaired_at: datetime | None
    completed_at: datetime | None


class CreateRepairIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=32)
    sku: str = Field(min_length=1, max_length=64)
    issue: str = Field(min_length=1, max_length=2000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)


class RepairTrackingIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=64)
    tracking_no: str = Field(min_length=1, max_length=64)


class RejectRepairIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class CompleteRepairIn(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=1000)
    return_carrier: str = Field(min_length=1, max_length=64)
    return_tracking_no: str = Field(min_length=1, max_length=64)
    parts_cost_cents: int = Field(default=0, ge=0)
    parts_note: str = Field(default="", max_length=500)


class RepairQuoteIn(BaseModel):
    amount_cents: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=1000)


class RepairEligibilityItemOut(BaseModel):
    sku: str
    title: str
    warranty_months: int
    deadline: datetime | None
    eligible: bool
    reason: str


class RepairEligibilityOut(BaseModel):
    order_no: str
    delivered_at: datetime | None
    items: list[RepairEligibilityItemOut]


def _out(view: RepairView) -> RepairOut:
    return RepairOut(
        id=view.id,
        order_no=view.order_no,
        sku=view.sku,
        title=view.title,
        status=view.status,
        issue=view.issue,
        attachment_ids=list(view.attachment_ids),
        carrier=view.carrier,
        tracking_no=view.tracking_no,
        return_carrier=view.return_carrier,
        return_tracking_no=view.return_tracking_no,
        return_address=view.return_address,
        rejected_reason=view.rejected_reason,
        resolution_note=view.resolution_note,
        quote_cents=view.quote_cents,
        quote_status=view.quote_status,
        quote_note=view.quote_note,
        quote_created_at=view.quote_created_at,
        quote_revised_at=view.quote_revised_at,
        quote_responded_at=view.quote_responded_at,
        quote_paid_at=view.quote_paid_at,
        quote_payment_ref=view.quote_payment_ref,
        parts_cost_cents=None,
        parts_note=None,
        created_at=view.created_at,
        approved_at=view.approved_at,
        received_at=view.received_at,
        repaired_at=view.repaired_at,
        completed_at=view.completed_at,
    )


def _admin_out(view: RepairView) -> RepairOut:
    return _out(view).model_copy(
        update={
            "parts_cost_cents": view.parts_cost_cents,
            "parts_note": view.parts_note,
        }
    )


@router.get("/api/store/repairs", response_model=list[RepairOut])
def customer_repairs(
    customer: CurrentCustomer, session: SessionDep
) -> list[RepairOut]:
    return [
        _out(view) for view in list_repairs_for_customer(session, customer)
    ]


@router.get(
    "/api/store/orders/{order_no}/repair-eligibility",
    response_model=RepairEligibilityOut,
)
def customer_repair_eligibility(
    order_no: str,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairEligibilityOut:
    view = repair_eligibility(session, customer, order_no)
    return RepairEligibilityOut(
        order_no=view.order_no,
        delivered_at=view.delivered_at,
        items=[
            RepairEligibilityItemOut(
                sku=item.sku,
                title=item.title,
                warranty_months=item.warranty_months,
                deadline=item.deadline,
                eligible=item.eligible,
                reason=item.reason,
            )
            for item in view.items
        ],
    )


@router.post("/api/store/repairs", response_model=RepairOut)
def customer_create_repair(
    payload: CreateRepairIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(
        create_repair(
            session,
            customer,
            payload.order_no,
            payload.sku,
            payload.issue,
            payload.attachment_ids,
        )
    )


@router.post("/api/store/repairs/{repair_id}/withdraw", response_model=RepairOut)
def customer_withdraw_repair(
    repair_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(withdraw_repair(session, customer, repair_id))


@router.post("/api/store/repairs/{repair_id}/tracking", response_model=RepairOut)
def customer_repair_tracking(
    repair_id: int,
    payload: RepairTrackingIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(
        add_repair_tracking(
            session,
            customer,
            repair_id,
            payload.carrier,
            payload.tracking_no,
        )
    )


@router.post(
    "/api/store/repairs/{repair_id}/quote/accept", response_model=RepairOut
)
def customer_accept_repair_quote(
    repair_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(accept_repair_quote(session, customer, repair_id))


@router.post(
    "/api/store/repairs/{repair_id}/quote/decline", response_model=RepairOut
)
def customer_decline_repair_quote(
    repair_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(decline_repair_quote(session, customer, repair_id))


@router.post(
    "/api/store/repairs/{repair_id}/quote/pay", response_model=RepairOut
)
def customer_pay_repair_quote(
    repair_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(pay_repair_quote(session, customer, repair_id))


@router.get("/api/admin/repairs", response_model=list[RepairOut])
def admin_repairs(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal[
            "active",
            "requested",
            "approved",
            "rejected",
            "in_transit",
            "received",
            "completed",
            "withdrawn",
            "all",
        ],
        Query(),
    ] = "active",
) -> list[RepairOut]:
    return [
        _admin_out(view)
        for view in list_repairs_for_admin(
            session, admin.merchant_id, status
        )
    ]


@router.post("/api/admin/repairs/{repair_id}/approve", response_model=RepairOut)
def admin_repair_approve(
    repair_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> RepairOut:
    return _admin_out(
        approve_repair(session, admin.merchant_id, admin.user_id, repair_id)
    )


@router.post("/api/admin/repairs/{repair_id}/reject", response_model=RepairOut)
def admin_repair_reject(
    repair_id: int,
    payload: RejectRepairIn,
    admin: OwnerDep,
    session: SessionDep,
) -> RepairOut:
    return _admin_out(
        reject_repair(
            session,
            admin.merchant_id,
            admin.user_id,
            repair_id,
            payload.reason,
        )
    )


@router.post("/api/admin/repairs/{repair_id}/receive", response_model=RepairOut)
def admin_repair_receive(
    repair_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> RepairOut:
    return _admin_out(
        receive_repair(session, admin.merchant_id, admin.user_id, repair_id)
    )


@router.post("/api/admin/repairs/{repair_id}/quote", response_model=RepairOut)
def admin_repair_quote(
    repair_id: int,
    payload: RepairQuoteIn,
    admin: OwnerDep,
    session: SessionDep,
) -> RepairOut:
    return _admin_out(
        quote_repair(
            session,
            admin.merchant_id,
            admin.user_id,
            repair_id,
            payload.amount_cents,
            payload.note,
        )
    )


@router.post("/api/admin/repairs/{repair_id}/complete", response_model=RepairOut)
def admin_repair_complete(
    repair_id: int,
    payload: CompleteRepairIn,
    admin: OwnerDep,
    session: SessionDep,
) -> RepairOut:
    return _admin_out(
        complete_repair(
            session,
            admin.merchant_id,
            admin.user_id,
            repair_id,
            payload.resolution_note,
            payload.return_carrier,
            payload.return_tracking_no,
            payload.parts_cost_cents,
            payload.parts_note,
        )
    )


@router.post(
    "/api/store/repairs/{repair_id}/confirm-receipt",
    response_model=RepairOut,
)
def customer_confirm_repair_received(
    repair_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> RepairOut:
    return _out(confirm_repair_received(session, customer, repair_id))
