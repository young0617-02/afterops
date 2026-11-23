from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, Field, model_validator

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import CurrentCustomer, SessionDep
from app.services.return_analytics import return_analytics
from app.services.return_inspection_report import (
    return_inspection_report_csv,
)
from app.services.return_policy import (
    ReturnPolicyView,
    get_return_policy,
    update_return_policy,
)
from app.services.returns import (
    ReturnEligibility,
    ReturnView,
    add_return_attachments,
    add_return_inspection_attachment,
    add_return_tracking,
    advance_return_tracking,
    approve_return,
    cancel_exchange_supplement,
    cancel_exchange_supplement_by_staff,
    cancel_return_pickup,
    cancel_return_pickup_by_staff,
    count_returns,
    create_return,
    create_return_appeal,
    extend_exchange_supplement_deadline,
    list_returns_for_admin,
    list_returns_for_customer,
    mark_return_pickup_picked_up,
    pay_exchange_supplement,
    receive_return,
    reject_return,
    report_return_tracking_exception,
    resolve_return_appeal,
    return_eligibility,
    schedule_return_pickup,
    withdraw_return,
    withdraw_return_appeal,
)
from app.settings import settings

router = APIRouter(tags=["returns"])


class ReturnItemOut(BaseModel):
    id: int
    sku: str
    title: str
    qty: int
    exchange_sku: str | None
    exchange_unit_price_cents: int | None
    disposition: Literal["restock", "damaged"] | None
    inspection_deduction_cents: int
    responsibility: Literal[
        "merchant", "customer", "logistics", "other"
    ] | None


class ReturnTrackingEventOut(BaseModel):
    status: str
    label: str
    description: str
    happened_at: datetime


class ReplacementShipmentEventOut(BaseModel):
    node: str
    description: str
    happened_at: datetime


class ReplacementShipmentOut(BaseModel):
    id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    events: list[ReplacementShipmentEventOut]


class ReturnAppealOut(BaseModel):
    id: int
    status: str
    reason: str
    attachment_ids: list[int]
    requested_cents: int
    approved_cents: int | None
    resolution_note: str
    created_at: datetime
    resolved_at: datetime | None


class ReturnPickupOut(BaseModel):
    id: int
    pickup_no: str
    status: str
    scheduled_date: date
    time_window: str
    address_recipient: str
    address_phone: str
    address_region: str
    address_city: str
    address_detail: str
    courier_name: str
    courier_phone: str
    cancel_reason: str
    picked_up_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


class ReturnOut(BaseModel):
    id: int
    order_no: str
    customer_phone: str | None
    customer_risk_level: str | None
    customer_risk_reasons: list[str]
    status: str
    reason: str
    reason_category: str
    resolution: str
    exchange_sku: str | None
    exchange_unit_price_cents: int | None
    replacement_order_no: str | None
    replacement_shipments: list[ReplacementShipmentOut]
    exchange_supplement_cents: int
    exchange_payment_status: str
    exchange_payment_ref: str
    exchange_paid_at: datetime | None
    exchange_cancelled_at: datetime | None
    exchange_cancel_reason: str
    exchange_cancelled_by: str
    exchange_payment_deadline: datetime | None
    appeal: ReturnAppealOut | None
    appeal_deadline: datetime | None
    can_appeal: bool
    pickup: ReturnPickupOut | None
    can_schedule_pickup: bool
    return_address: str
    carrier: str
    tracking_no: str
    shipment_status: str
    tracking_events: list[ReturnTrackingEventOut]
    rejected_reason: str
    refund_proposal_id: int | None
    refund_amount_cents: int | None
    inspection_deduction_cents: int
    inspection_note: str
    attachment_ids: list[int]
    inspection_attachment_ids: list[int]
    created_at: datetime
    approved_at: datetime | None
    received_at: datetime | None
    items: list[ReturnItemOut]


class ReturnListOut(BaseModel):
    items: list[ReturnOut]
    counts: dict[str, int]


class RestockItemOut(BaseModel):
    sku: str
    warehouse_id: str
    qty: int


class DamagedItemOut(BaseModel):
    sku: str
    title: str
    qty: int


class ReceiveReturnOut(ReturnOut):
    restocked_items: list[RestockItemOut]
    damaged_items: list[DamagedItemOut]


class ReturnDispositionIn(BaseModel):
    item_id: int = Field(gt=0)
    disposition: Literal["restock", "damaged"]
    deduction_cents: int = Field(default=0, ge=0)
    responsibility: Literal[
        "merchant", "customer", "logistics", "other"
    ] | None = None


class ReceiveReturnIn(BaseModel):
    restock: bool = True
    deduction_cents: int = Field(default=0, ge=0)
    inspection_note: str = Field(default="", max_length=1000)
    items: list[ReturnDispositionIn] = Field(
        default_factory=list, max_length=50
    )

    @model_validator(mode="after")
    def reject_duplicate_items(self) -> ReceiveReturnIn:
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("退货明细不能重复")
        return self


class ReturnItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    qty: int = Field(ge=1)
    exchange_sku: str | None = Field(default=None, max_length=64)


class CreateReturnIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=1000)
    reason_category: Literal[
        "quality",
        "damaged",
        "wrong_item",
        "size",
        "not_needed",
        "other",
    ] = "other"
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)
    resolution: Literal["refund", "exchange"] = "refund"
    exchange_sku: str | None = Field(default=None, max_length=64)
    items: list[ReturnItemIn] = Field(default_factory=list, max_length=20)


class TrackingIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=64)
    tracking_no: str = Field(min_length=1, max_length=64)


class AdvanceReturnTrackingIn(BaseModel):
    status: Literal[
        "picked_up", "in_transit", "out_for_delivery", "delivered"
    ]
    description: str = Field(default="", max_length=500)


class ReturnTrackingExceptionIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)


class ReturnAttachmentsIn(BaseModel):
    attachment_ids: list[int] = Field(min_length=1, max_length=3)


class ReturnAppealIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)


class ReturnAppealAcceptIn(BaseModel):
    approved_cents: int = Field(gt=0)
    note: str = Field(default="", max_length=1000)


class ReturnAppealRejectIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class ExchangeCancellationIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class ExtendExchangeSupplementIn(BaseModel):
    hours: int = Field(default=24, ge=1, le=72)


class ReturnPickupIn(BaseModel):
    scheduled_date: date
    time_window: Literal["09:00-12:00", "13:00-18:00", "18:00-21:00"]


class ReturnPickupCancelIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class AdminReturnPickupCancelIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class RejectReturnIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class CategoryWindowOut(BaseModel):
    category: str
    window_days: int


class CategoryWindowIn(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    window_days: int = Field(ge=0, le=90)


class ReturnPolicyOut(BaseModel):
    window_days: int
    return_shipping_fee_cents: int
    allow_exchange: bool
    final_sale_categories: list[str]
    category_windows: list[CategoryWindowOut]
    updated_at: datetime | None


class ReturnPolicyIn(BaseModel):
    window_days: int = Field(ge=0, le=90)
    return_shipping_fee_cents: int = Field(default=0, ge=0, le=20000)
    allow_exchange: bool
    final_sale_categories: list[str] = Field(default_factory=list, max_length=20)
    category_windows: list[CategoryWindowIn] = Field(
        default_factory=list, max_length=20
    )


class ReturnEligibilityItemOut(BaseModel):
    sku: str
    title: str
    category: str
    window_days: int
    deadline: datetime | None
    eligible: bool
    reason: str


class ReturnEligibilityOut(BaseModel):
    eligible: bool
    reason: str
    window_days: int
    return_shipping_fee_cents: int
    delivered_at: datetime | None
    deadline: datetime | None
    allow_exchange: bool
    final_sale_skus: list[str]
    items: list[ReturnEligibilityItemOut]


def _out(view: ReturnView) -> ReturnOut:
    return ReturnOut(**asdict(view))


def _policy_out(view: ReturnPolicyView) -> ReturnPolicyOut:
    return ReturnPolicyOut(
        window_days=view.window_days,
        return_shipping_fee_cents=view.return_shipping_fee_cents,
        allow_exchange=view.allow_exchange,
        final_sale_categories=list(view.final_sale_categories),
        category_windows=[
            CategoryWindowOut(
                category=item.category,
                window_days=item.window_days,
            )
            for item in view.category_windows
        ],
        updated_at=view.updated_at,
    )


def _eligibility_out(view: ReturnEligibility) -> ReturnEligibilityOut:
    return ReturnEligibilityOut(
        eligible=view.eligible,
        reason=view.reason,
        window_days=view.window_days,
        return_shipping_fee_cents=view.return_shipping_fee_cents,
        delivered_at=view.delivered_at,
        deadline=view.deadline,
        allow_exchange=view.allow_exchange,
        final_sale_skus=list(view.final_sale_skus),
        items=[
            ReturnEligibilityItemOut(
                sku=item.sku,
                title=item.title,
                category=item.category,
                window_days=item.window_days,
                deadline=item.deadline,
                eligible=item.eligible,
                reason=item.reason,
            )
            for item in view.items
        ],
    )


@router.get("/api/store/returns", response_model=list[ReturnOut])
def customer_returns(
    customer: CurrentCustomer, session: SessionDep
) -> list[ReturnOut]:
    return [
        _out(view)
        for view in list_returns_for_customer(session, customer)
    ]


@router.get("/api/store/return-policy", response_model=ReturnPolicyOut)
def customer_return_policy(
    customer: CurrentCustomer, session: SessionDep
) -> ReturnPolicyOut:
    return _policy_out(get_return_policy(session, customer.merchant_id))


@router.get("/api/store/returns/eligibility", response_model=ReturnEligibilityOut)
def customer_return_eligibility(
    customer: CurrentCustomer,
    session: SessionDep,
    order_no: Annotated[str, Query(min_length=1, max_length=32)],
    resolution: Annotated[Literal["refund", "exchange"], Query()] = "refund",
) -> ReturnEligibilityOut:
    return _eligibility_out(
        return_eligibility(session, customer, order_no, resolution)
    )


@router.post("/api/store/returns", response_model=ReturnOut)
def customer_create_return(
    payload: CreateReturnIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        create_return(
            session,
            customer,
            payload.order_no,
            payload.reason,
            payload.attachment_ids,
            payload.resolution,
            payload.exchange_sku,
            items=(
                [
                    (item.sku, item.qty, item.exchange_sku)
                    for item in payload.items
                ]
                if payload.items
                else None
            ),
            reason_category=payload.reason_category,
        )
    )


@router.post("/api/store/returns/{return_id}/withdraw", response_model=ReturnOut)
def customer_withdraw_return(
    return_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(withdraw_return(session, customer, return_id))


@router.post(
    "/api/store/returns/{return_id}/attachments", response_model=ReturnOut
)
def customer_return_attachments(
    return_id: int,
    payload: ReturnAttachmentsIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        add_return_attachments(
            session, customer, return_id, payload.attachment_ids
        )
    )


@router.post(
    "/api/store/returns/{return_id}/appeal", response_model=ReturnOut
)
def customer_return_appeal(
    return_id: int,
    payload: ReturnAppealIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        create_return_appeal(
            session,
            customer,
            return_id,
            payload.reason,
            payload.attachment_ids,
        )
    )


@router.post(
    "/api/store/returns/{return_id}/appeal/withdraw", response_model=ReturnOut
)
def customer_withdraw_return_appeal(
    return_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(withdraw_return_appeal(session, customer, return_id))


@router.post(
    "/api/store/returns/{return_id}/exchange-supplement/pay",
    response_model=ReturnOut,
)
def customer_pay_exchange_supplement(
    return_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(pay_exchange_supplement(session, customer, return_id))


@router.post(
    "/api/store/returns/{return_id}/exchange/cancel",
    response_model=ReturnOut,
)
def customer_cancel_exchange(
    return_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
    payload: ExchangeCancellationIn | None = None,
) -> ReturnOut:
    return _out(
        cancel_exchange_supplement(
            session,
            customer,
            return_id,
            payload.reason if payload else "",
        )
    )


@router.post(
    "/api/store/returns/{return_id}/pickup", response_model=ReturnOut
)
def customer_return_pickup(
    return_id: int,
    payload: ReturnPickupIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        schedule_return_pickup(
            session,
            customer,
            return_id,
            payload.scheduled_date,
            payload.time_window,
        )
    )


@router.post(
    "/api/store/returns/{return_id}/pickup/cancel", response_model=ReturnOut
)
def customer_cancel_return_pickup(
    return_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
    payload: ReturnPickupCancelIn | None = None,
) -> ReturnOut:
    return _out(
        cancel_return_pickup(
            session,
            customer,
            return_id,
            payload.reason if payload else "",
        )
    )


@router.post("/api/store/returns/{return_id}/tracking", response_model=ReturnOut)
def customer_return_tracking(
    return_id: int,
    payload: TrackingIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        add_return_tracking(
            session,
            customer,
            return_id,
            payload.carrier,
            payload.tracking_no,
        )
    )


@router.get("/api/admin/return-policy", response_model=ReturnPolicyOut)
def admin_return_policy(admin: AdminDep, session: SessionDep) -> ReturnPolicyOut:
    return _policy_out(get_return_policy(session, admin.merchant_id))


@router.patch("/api/admin/return-policy", response_model=ReturnPolicyOut)
def admin_update_return_policy(
    payload: ReturnPolicyIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnPolicyOut:
    return _policy_out(
        update_return_policy(
            session,
            admin.merchant_id,
            admin.user_id,
            window_days=payload.window_days,
            return_shipping_fee_cents=payload.return_shipping_fee_cents,
            allow_exchange=payload.allow_exchange,
            final_sale_categories=payload.final_sale_categories,
            category_windows=[
                item.model_dump() for item in payload.category_windows
            ],
        )
    )


class ReturnReasonStatOut(BaseModel):
    category: str
    label: str
    count: int


class ReturnSkuStatOut(BaseModel):
    sku: str
    title: str
    requests: int
    qty: int


class ReturnResponsibilityStatOut(BaseModel):
    responsibility: str
    label: str
    items: int
    qty: int
    deduction_cents: int


class ReturnAnalyticsOut(BaseModel):
    days: int
    since: datetime
    total: int
    status_counts: dict[str, int]
    reasons: list[ReturnReasonStatOut]
    top_skus: list[ReturnSkuStatOut]
    responsibilities: list[ReturnResponsibilityStatOut]
    refund_total_cents: int
    approval_rate: float
    avg_approval_hours: float | None
    restocked_units: int
    damaged_units: int
    damaged_cost_cents: int


@router.get(
    "/api/admin/returns/analytics", response_model=ReturnAnalyticsOut
)
def admin_return_analytics(
    admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ReturnAnalyticsOut:
    result = return_analytics(session, admin.merchant_id, days)
    return ReturnAnalyticsOut(
        days=result.days,
        since=result.since,
        total=result.total,
        status_counts=result.status_counts,
        reasons=[
            ReturnReasonStatOut(
                category=item.category,
                label=item.label,
                count=item.count,
            )
            for item in result.reasons
        ],
        top_skus=[
            ReturnSkuStatOut(
                sku=item.sku,
                title=item.title,
                requests=item.requests,
                qty=item.qty,
            )
            for item in result.top_skus
        ],
        responsibilities=[
            ReturnResponsibilityStatOut(
                responsibility=item.responsibility,
                label=item.label,
                items=item.items,
                qty=item.qty,
                deduction_cents=item.deduction_cents,
            )
            for item in result.responsibilities
        ],
        refund_total_cents=result.refund_total_cents,
        approval_rate=result.approval_rate,
        avg_approval_hours=result.avg_approval_hours,
        restocked_units=result.restocked_units,
        damaged_units=result.damaged_units,
        damaged_cost_cents=result.damaged_cost_cents,
    )


@router.get("/api/admin/returns/inspection-report.csv")
def admin_return_inspection_report(
    admin: OwnerDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> Response:
    csv_text = return_inspection_report_csv(
        session, admin.merchant_id, days
    )
    return Response(
        content=("\ufeff" + csv_text).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="return-inspection-report.csv"'
            )
        },
    )


@router.get("/api/admin/returns", response_model=ReturnListOut)
def admin_returns(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal[
            "active",
            "requested",
            "approved",
            "in_transit",
            "received",
            "rejected",
            "withdrawn",
            "exchange_cancelled",
            "appeal",
            "pickup",
            "all",
        ],
        Query(),
    ] = "active",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ReturnListOut:
    return ReturnListOut(
        items=[
            _out(view)
            for view in list_returns_for_admin(
                session, admin.merchant_id, status=status, limit=limit
            )
        ],
        counts=count_returns(session, admin.merchant_id),
    )


@router.post("/api/admin/returns/{return_id}/approve", response_model=ReturnOut)
def admin_return_approve(
    return_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        approve_return(session, admin.merchant_id, admin.user_id, return_id)
    )


@router.post("/api/admin/returns/{return_id}/reject", response_model=ReturnOut)
def admin_return_reject(
    return_id: int,
    payload: RejectReturnIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        reject_return(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            payload.reason,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/exchange-supplement/extend",
    response_model=ReturnOut,
)
def admin_extend_exchange_supplement(
    return_id: int,
    payload: ExtendExchangeSupplementIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        extend_exchange_supplement_deadline(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            payload.hours,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/exchange/cancel",
    response_model=ReturnOut,
)
def admin_cancel_exchange(
    return_id: int,
    admin: OwnerDep,
    session: SessionDep,
    payload: ExchangeCancellationIn | None = None,
) -> ReturnOut:
    return _out(
        cancel_exchange_supplement_by_staff(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            payload.reason if payload else "",
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/appeal/accept",
    response_model=ReturnOut,
)
def admin_return_appeal_accept(
    return_id: int,
    payload: ReturnAppealAcceptIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        resolve_return_appeal(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            accept=True,
            approved_cents=payload.approved_cents,
            note=payload.note,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/appeal/reject",
    response_model=ReturnOut,
)
def admin_return_appeal_reject(
    return_id: int,
    payload: ReturnAppealRejectIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        resolve_return_appeal(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            accept=False,
            note=payload.note,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/pickup/picked-up",
    response_model=ReturnOut,
)
def admin_return_pickup_picked_up(
    return_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        mark_return_pickup_picked_up(
            session, admin.merchant_id, admin.user_id, return_id
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/pickup/cancel",
    response_model=ReturnOut,
)
def admin_return_pickup_cancel(
    return_id: int,
    payload: AdminReturnPickupCancelIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        cancel_return_pickup_by_staff(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            payload.note,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/tracking/advance",
    response_model=ReturnOut,
)
def admin_return_tracking_advance(
    return_id: int,
    payload: AdvanceReturnTrackingIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        advance_return_tracking(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            payload.status,
            payload.description,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/inspection-attachments",
    response_model=ReturnOut,
)
async def admin_return_inspection_attachment(
    return_id: int,
    file: Annotated[UploadFile, File()],
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    data = await file.read(settings.upload_max_bytes + 1)
    return _out(
        add_return_inspection_attachment(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            original_name=file.filename or "inspection",
            data=data,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/tracking/exception",
    response_model=ReturnOut,
)
def admin_return_tracking_exception(
    return_id: int,
    payload: ReturnTrackingExceptionIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ReturnOut:
    return _out(
        report_return_tracking_exception(
            session,
            admin.merchant_id,
            admin.user_id,
            return_id,
            payload.description,
        )
    )


@router.post(
    "/api/admin/returns/{return_id}/receive", response_model=ReceiveReturnOut
)
def admin_return_receive(
    return_id: int,
    admin: OwnerDep,
    session: SessionDep,
    payload: ReceiveReturnIn | None = None,
) -> ReceiveReturnOut:
    dispositions = (
        {item.item_id: item.disposition for item in payload.items}
        if payload and payload.items
        else None
    )
    item_deductions = (
        {
            item.item_id: item.deduction_cents
            for item in payload.items
            if item.deduction_cents > 0
        }
        if payload and payload.items
        else None
    )
    responsibilities = (
        {
            item.item_id: item.responsibility
            for item in payload.items
            if item.responsibility is not None
        }
        if payload and payload.items
        else None
    )
    result = receive_return(
        session,
        admin.merchant_id,
        admin.user_id,
        return_id,
        restock=payload.restock if payload else True,
        dispositions=dispositions,
        deduction_cents=payload.deduction_cents if payload else 0,
        inspection_note=payload.inspection_note if payload else "",
        item_deductions=item_deductions,
        responsibilities=responsibilities,
    )
    return ReceiveReturnOut(
        **_out(result.view).model_dump(),
        restocked_items=[
            RestockItemOut(
                sku=line.sku,
                warehouse_id=line.warehouse_id,
                qty=line.qty,
            )
            for line in result.restocked
        ],
        damaged_items=[
            DamagedItemOut(sku=line.sku, title=line.title, qty=line.qty)
            for line in result.damaged
        ],
    )
