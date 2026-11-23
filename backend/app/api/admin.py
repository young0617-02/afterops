from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import SessionDep
from app.domain.models import User
from app.domain.money import format_cents
from app.services import rate_limit
from app.services.audit import (
    count_events,
    list_event_types,
    list_events,
    verify_chain_report,
)
from app.services.auth import (
    ADMIN_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    sign_admin_token,
    verify_admin_token,
)
from app.services.csrf import ADMIN_CSRF_COOKIE, set_csrf_cookie
from app.services.errors import (
    ForbiddenError,
    UnauthorizedError,
)
from app.services.fulfillment import (
    advance_shipment,
    create_shipment,
    list_fulfillment_orders,
)
from app.services.handoffs import (
    HandoffTicketView,
    add_handoff_note,
    assign_ticket,
    claim_ticket,
    count_tickets,
    get_ticket_detail,
    list_tickets,
    refresh_ticket_summary,
    resolve_ticket,
    send_agent_message,
)
from app.services.merchant import demo_merchant_id
from app.services.passwords import verify_password
from app.services.proposals import (
    approve_proposal,
    list_proposals,
    proposal_to_dict,
    reject_proposal,
)
from app.services.staff import set_handoff_availability
from app.settings import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


@dataclass(frozen=True)
class AdminContext:
    merchant_id: int
    user_id: int
    role: str
    email: str
    handoff_available: bool


def get_admin_context(request: Request, session: SessionDep) -> AdminContext:
    payload = verify_admin_token(
        request.cookies.get(ADMIN_COOKIE_NAME, ""), settings.session_secret
    )
    if payload is None:
        raise UnauthorizedError("请先登录管理后台")
    try:
        user_id = int(payload["uid"])
        merchant_id = int(payload["mid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("管理员会话无效") from exc
    user = session.get(User, user_id)
    if (
        user is None
        or user.merchant_id != merchant_id
        or user.status != "active"
    ):
        raise UnauthorizedError("管理员会话无效")
    return AdminContext(
        merchant_id=user.merchant_id,
        user_id=user.id,
        role=user.role,
        email=user.email,
        handoff_available=user.handoff_available,
    )


AdminDep = Annotated[AdminContext, Depends(get_admin_context)]


def require_owner(admin: AdminDep) -> AdminContext:
    if admin.role != "owner":
        raise ForbiddenError("只有店主可以执行该操作")
    return admin


OwnerDep = Annotated[AdminContext, Depends(require_owner)]


class AdminLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class AdminMeOut(BaseModel):
    user_id: int
    email: str
    role: str
    handoff_available: bool


class AvailabilityIn(BaseModel):
    available: bool


class RejectIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class ShipmentEventOut(BaseModel):
    happened_at: datetime
    node: str
    description: str


class ShipmentPackageItemOut(BaseModel):
    order_item_id: int
    sku: str
    title: str
    qty: int


class AdminShipmentOut(BaseModel):
    id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    items: list[ShipmentPackageItemOut]
    events: list[ShipmentEventOut]


class AdminFulfillmentItemOut(BaseModel):
    order_item_id: int
    sku: str
    title: str
    qty: int
    shipped_qty: int
    cancelled_qty: int
    remaining_qty: int


class AdminOrderOut(BaseModel):
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    payable_cents: int
    payable_display: str
    placed_at: datetime
    recipient_name: str
    recipient_phone: str
    address: str
    item_count: int
    items: list[AdminFulfillmentItemOut]
    shipments: list[AdminShipmentOut]


class ShipmentItemIn(BaseModel):
    order_item_id: int = Field(ge=1)
    qty: int = Field(ge=1)


class CreateShipmentIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=64)
    tracking_no: str = Field(min_length=1, max_length=64)
    items: list[ShipmentItemIn] | None = None


class AdvanceShipmentIn(BaseModel):
    status: Literal["in_transit", "out_for_delivery", "delivered", "exception"]
    node: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=500)
    shipment_id: int | None = None


class ShipmentActionOut(BaseModel):
    order_no: str
    shipment_id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    item_count: int
    order_status: str
    fulfillment_status: str


class AuditEventOut(BaseModel):
    id: int
    event_type: str
    payload: dict[str, Any]
    conversation_id: int | None
    proposal_id: int | None
    prev_hash: str
    row_hash: str
    created_at: datetime


class AuditListOut(BaseModel):
    items: list[AuditEventOut]
    has_more: bool
    next_before_id: int | None
    event_types: list[str]
    total: int


class AuditVerifyOut(BaseModel):
    valid: bool
    checked_events: int
    broken_at: int | None


class HandoffTicketOut(BaseModel):
    id: int
    ticket_ref: str
    queue: str
    status: str
    priority: str
    intent: str
    last_user_message: str
    last_reply: str
    summary: str
    summary_generated_at: datetime | None
    customer_phone: str | None
    customer_name: str | None
    conversation_id: int
    claimed_by_user_id: int | None
    claimed_by_email: str | None
    claimed_at: datetime | None
    resolved_by_user_id: int | None
    resolved_by_email: str | None
    resolved_at: datetime | None
    resolution_note: str
    escalated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class HandoffListOut(BaseModel):
    items: list[HandoffTicketOut]
    counts: dict[str, int]


class HandoffMessageOut(BaseModel):
    id: int
    role: str
    content: str
    image_urls: list[str]
    created_at: datetime


class HandoffNoteOut(BaseModel):
    id: int
    author_email: str | None
    content: str
    created_at: datetime


class HandoffCustomerOrderOut(BaseModel):
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    payable_cents: int
    payable_display: str
    placed_at: datetime


class HandoffCustomerContextOut(BaseModel):
    order_count: int
    last_order_at: datetime | None
    after_sales_count: int
    pending_proposals: int
    active_returns: int
    recent_orders: list[HandoffCustomerOrderOut]


class HandoffDetailOut(HandoffTicketOut):
    messages: list[HandoffMessageOut]
    notes: list[HandoffNoteOut]
    customer_context: HandoffCustomerContextOut


class HandoffActionOut(BaseModel):
    id: int
    ticket_ref: str
    status: str


class ResolveHandoffIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class ReplyHandoffIn(BaseModel):
    content: str = Field(default="", max_length=1000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def require_content_or_attachment(self) -> ReplyHandoffIn:
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("回复内容或图片至少提供一项")
        return self


class HandoffReplyOut(BaseModel):
    message_id: int
    ticket_id: int
    ticket_ref: str
    status: str
    content: str
    created_at: datetime
    auto_claimed: bool


class AssignHandoffIn(BaseModel):
    user_id: int = Field(gt=0)


def _handoff_out(view: HandoffTicketView) -> HandoffTicketOut:
    return HandoffTicketOut(
        id=view.id,
        ticket_ref=view.ticket_ref,
        queue=view.queue,
        status=view.status,
        priority=view.priority,
        intent=view.intent,
        last_user_message=view.last_user_message,
        last_reply=view.last_reply,
        summary=view.summary,
        summary_generated_at=view.summary_generated_at,
        customer_phone=view.customer_phone,
        customer_name=view.customer_name,
        conversation_id=view.conversation_id,
        claimed_by_user_id=view.claimed_by_user_id,
        claimed_by_email=view.claimed_by_email,
        claimed_at=view.claimed_at,
        resolved_by_user_id=view.resolved_by_user_id,
        resolved_by_email=view.resolved_by_email,
        resolved_at=view.resolved_at,
        resolution_note=view.resolution_note,
        escalated_at=view.escalated_at,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.post("/session", response_model=AdminMeOut)
def admin_login(
    payload: AdminLoginIn,
    response: Response,
    request: Request,
    session: SessionDep,
) -> AdminMeOut:
    merchant_id = demo_merchant_id(session)
    client_ip = request.client.host if request.client else "unknown"
    rate_limit.check(
        session,
        f"admin_login:{client_ip}:{payload.email.strip().lower()}",
        settings.admin_login_limit,
        settings.admin_login_window_seconds,
    )
    user = session.execute(
        select(User).where(
            User.merchant_id == merchant_id,
            User.email == payload.email.strip().lower(),
            User.status == "active",
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("邮箱或密码错误")
    user.last_seen_at = datetime.now(UTC)
    session.commit()
    token = sign_admin_token(
        user.id, user.merchant_id, user.role, settings.session_secret
    )
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "prod",
        path="/",
    )
    set_csrf_cookie(response, ADMIN_CSRF_COOKIE, token)
    return AdminMeOut(
        user_id=user.id,
        email=user.email,
        role=user.role,
        handoff_available=user.handoff_available,
    )


@router.get("/me", response_model=AdminMeOut)
def admin_me(admin: AdminDep) -> AdminMeOut:
    return AdminMeOut(
        user_id=admin.user_id,
        email=admin.email,
        role=admin.role,
        handoff_available=admin.handoff_available,
    )


@router.patch("/me/availability", response_model=AdminMeOut)
def admin_set_availability(
    payload: AvailabilityIn,
    admin: AdminDep,
    session: SessionDep,
) -> AdminMeOut:
    user = set_handoff_availability(
        session,
        admin.merchant_id,
        admin.user_id,
        payload.available,
    )
    return AdminMeOut(
        user_id=user.id,
        email=user.email,
        role=user.role,
        handoff_available=user.handoff_available,
    )


@router.get("/audit/verify", response_model=AuditVerifyOut)
def admin_audit_verify(admin: AdminDep, session: SessionDep) -> AuditVerifyOut:
    valid, broken, checked = verify_chain_report(session, admin.merchant_id)
    return AuditVerifyOut(
        valid=valid,
        checked_events=checked,
        broken_at=broken if isinstance(broken, int) else None,
    )


@router.get("/audit/export")
def admin_audit_export(
    admin: AdminDep,
    session: SessionDep,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    proposal_id: Annotated[int | None, Query(ge=1)] = None,
    conversation_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> Response:
    events, has_more = list_events(
        session,
        admin.merchant_id,
        event_type=event_type,
        q=q,
        proposal_id=proposal_id,
        conversation_id=conversation_id,
        limit=limit,
    )
    valid, broken, checked = verify_chain_report(session, admin.merchant_id)
    exported_at = datetime.now(UTC)
    payload = {
        "exported_at": exported_at.isoformat(),
        "merchant_id": admin.merchant_id,
        "filters": {
            "event_type": event_type,
            "q": q,
            "proposal_id": proposal_id,
            "conversation_id": conversation_id,
        },
        "audit_valid": valid,
        "audit_broken_at": broken if isinstance(broken, int) else None,
        "audit_checked_events": checked,
        "count": len(events),
        "truncated": has_more,
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload_json,
                "conversation_id": event.conversation_id,
                "proposal_id": event.proposal_id,
                "prev_hash": event.prev_hash,
                "row_hash": event.row_hash,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }
    filename = f"afterops-audit-{exported_at.strftime('%Y%m%d%H%M%S')}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit", response_model=AuditListOut)
def admin_audit(
    admin: AdminDep,
    session: SessionDep,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    proposal_id: Annotated[int | None, Query(ge=1)] = None,
    conversation_id: Annotated[int | None, Query(ge=1)] = None,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditListOut:
    events, has_more = list_events(
        session,
        admin.merchant_id,
        event_type=event_type,
        q=q,
        proposal_id=proposal_id,
        conversation_id=conversation_id,
        before_id=before_id,
        limit=limit,
    )
    return AuditListOut(
        items=[
            AuditEventOut(
                id=event.id,
                event_type=event.event_type,
                payload=event.payload_json,
                conversation_id=event.conversation_id,
                proposal_id=event.proposal_id,
                prev_hash=event.prev_hash,
                row_hash=event.row_hash,
                created_at=event.created_at,
            )
            for event in events
        ],
        has_more=has_more,
        next_before_id=events[-1].id if has_more and events else None,
        event_types=list_event_types(session, admin.merchant_id),
        total=count_events(
            session,
            admin.merchant_id,
            event_type=event_type,
            q=q,
            proposal_id=proposal_id,
            conversation_id=conversation_id,
        ),
    )


@router.get("/handoffs", response_model=HandoffListOut)
def admin_handoffs(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal["active", "open", "claimed", "resolved", "all"], Query()
    ] = "active",
    priority: Annotated[
        Literal["normal", "high", "urgent"] | None, Query()
    ] = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> HandoffListOut:
    views = list_tickets(
        session,
        admin.merchant_id,
        status=status,
        priority=priority,
        q=q,
        limit=limit,
    )
    return HandoffListOut(
        items=[_handoff_out(view) for view in views],
        counts=count_tickets(session, admin.merchant_id),
    )


@router.get("/handoffs/{ticket_id}", response_model=HandoffDetailOut)
def admin_handoff_detail(
    ticket_id: int,
    admin: AdminDep,
    session: SessionDep,
) -> HandoffDetailOut:
    detail = get_ticket_detail(session, admin.merchant_id, ticket_id)
    return HandoffDetailOut(
        **_handoff_out(detail.ticket).model_dump(),
        messages=[
            HandoffMessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                image_urls=[
                    f"/api/admin/attachments/{attachment_id}"
                    for attachment_id in message.attachment_ids
                ],
                created_at=message.created_at,
            )
            for message in detail.messages
        ],
        notes=[
            HandoffNoteOut(
                id=note.id,
                author_email=note.author_email,
                content=note.content,
                created_at=note.created_at,
            )
            for note in detail.notes
        ],
        customer_context=HandoffCustomerContextOut(
            order_count=detail.customer_context.customer.order_count,
            last_order_at=detail.customer_context.customer.last_order_at,
            after_sales_count=(
                detail.customer_context.customer.after_sales_count
            ),
            pending_proposals=(
                detail.customer_context.customer.pending_proposals
            ),
            active_returns=detail.customer_context.customer.active_returns,
            recent_orders=[
                HandoffCustomerOrderOut(
                    order_no=order.order_no,
                    status=order.status,
                    payment_status=order.payment_status,
                    fulfillment_status=order.fulfillment_status,
                    payable_cents=order.payable_cents,
                    payable_display=format_cents(order.payable_cents),
                    placed_at=order.placed_at,
                )
                for order in detail.customer_context.orders[:3]
            ],
        ),
    )


@router.post(
    "/handoffs/{ticket_id}/summary", response_model=HandoffTicketOut
)
def admin_handoff_summary(
    ticket_id: int,
    admin: AdminDep,
    session: SessionDep,
) -> HandoffTicketOut:
    return _handoff_out(
        refresh_ticket_summary(session, admin.merchant_id, ticket_id)
    )


@router.post("/handoffs/{ticket_id}/claim", response_model=HandoffActionOut)
def admin_handoff_claim(
    ticket_id: int,
    admin: AdminDep,
    session: SessionDep,
) -> HandoffActionOut:
    result = claim_ticket(
        session, admin.merchant_id, admin.user_id, ticket_id
    )
    return HandoffActionOut(**result.__dict__)


@router.post("/handoffs/{ticket_id}/assign", response_model=HandoffActionOut)
def admin_handoff_assign(
    ticket_id: int,
    payload: AssignHandoffIn,
    admin: OwnerDep,
    session: SessionDep,
) -> HandoffActionOut:
    result = assign_ticket(
        session,
        admin.merchant_id,
        admin.user_id,
        ticket_id,
        payload.user_id,
    )
    return HandoffActionOut(**result.__dict__)


@router.post("/handoffs/{ticket_id}/resolve", response_model=HandoffActionOut)
def admin_handoff_resolve(
    ticket_id: int,
    payload: ResolveHandoffIn,
    admin: AdminDep,
    session: SessionDep,
) -> HandoffActionOut:
    result = resolve_ticket(
        session,
        admin.merchant_id,
        admin.user_id,
        ticket_id,
        payload.note,
    )
    return HandoffActionOut(**result.__dict__)


@router.post(
    "/handoffs/{ticket_id}/messages", response_model=HandoffReplyOut
)
def admin_handoff_reply(
    ticket_id: int,
    payload: ReplyHandoffIn,
    admin: AdminDep,
    session: SessionDep,
) -> HandoffReplyOut:
    result = send_agent_message(
        session,
        admin.merchant_id,
        admin.user_id,
        ticket_id,
        payload.content,
        payload.attachment_ids,
    )
    return HandoffReplyOut(**result.__dict__)


@router.post(
    "/handoffs/{ticket_id}/notes", response_model=HandoffNoteOut
)
def admin_handoff_note(
    ticket_id: int,
    payload: ReplyHandoffIn,
    admin: AdminDep,
    session: SessionDep,
) -> HandoffNoteOut:
    note = add_handoff_note(
        session,
        admin.merchant_id,
        admin.user_id,
        ticket_id,
        payload.content,
    )
    return HandoffNoteOut(
        id=note.id,
        author_email=note.author_email,
        content=note.content,
        created_at=note.created_at,
    )


@router.get("/orders", response_model=list[AdminOrderOut])
def admin_orders(
    admin: AdminDep,
    session: SessionDep,
    open_only: Annotated[bool, Query()] = True,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AdminOrderOut]:
    views = list_fulfillment_orders(
        session,
        admin.merchant_id,
        open_only=open_only,
        q=q,
        limit=limit,
    )
    return [
        AdminOrderOut(
            order_no=view.order_no,
            status=view.status,
            payment_status=view.payment_status,
            fulfillment_status=view.fulfillment_status,
            payable_cents=view.payable_cents,
            payable_display=format_cents(view.payable_cents),
            placed_at=view.placed_at,
            recipient_name=view.recipient_name,
            recipient_phone=view.recipient_phone,
            address=" ".join(
                part
                for part in (
                    view.address_region,
                    view.address_city,
                    view.address_detail,
                )
                if part
            ),
            item_count=view.item_count,
            items=[
                AdminFulfillmentItemOut(
                    order_item_id=item.order_item_id,
                    sku=item.sku,
                    title=item.title,
                    qty=item.qty,
                    shipped_qty=item.shipped_qty,
                    cancelled_qty=item.cancelled_qty,
                    remaining_qty=item.remaining_qty,
                )
                for item in view.items
            ],
            shipments=[
                AdminShipmentOut(
                    id=shipment.id,
                    package_no=shipment.package_no,
                    carrier=shipment.carrier,
                    tracking_no=shipment.tracking_no,
                    status=shipment.status,
                    current_node=shipment.current_node,
                    shipped_at=shipment.shipped_at,
                    delivered_at=shipment.delivered_at,
                    items=[
                        ShipmentPackageItemOut(
                            order_item_id=item.order_item_id,
                            sku=item.sku,
                            title=item.title,
                            qty=item.qty,
                        )
                        for item in shipment.items
                    ],
                    events=[
                        ShipmentEventOut(
                            happened_at=event.happened_at,
                            node=event.node,
                            description=event.description,
                        )
                        for event in shipment.events
                    ],
                )
                for shipment in view.shipments
            ],
        )
        for view in views
    ]


@router.post("/orders/{order_no}/shipment", response_model=ShipmentActionOut)
def create_order_shipment(
    order_no: str,
    payload: CreateShipmentIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ShipmentActionOut:
    result = create_shipment(
        session,
        admin.merchant_id,
        admin.user_id,
        order_no,
        payload.carrier,
        payload.tracking_no,
        (
            [(item.order_item_id, item.qty) for item in payload.items]
            if payload.items is not None
            else None
        ),
    )
    return ShipmentActionOut(**result.__dict__)


@router.post("/orders/{order_no}/shipment/advance", response_model=ShipmentActionOut)
def advance_order_shipment(
    order_no: str,
    payload: AdvanceShipmentIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ShipmentActionOut:
    result = advance_shipment(
        session,
        admin.merchant_id,
        admin.user_id,
        order_no,
        payload.status,
        payload.node,
        payload.description,
        payload.shipment_id,
    )
    return ShipmentActionOut(**result.__dict__)


@router.delete("/session")
def admin_logout(response: Response) -> dict[str, str]:
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    response.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return {"status": "signed_out"}


@router.get("/proposals")
def get_proposals(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    return [
        proposal_to_dict(proposal)
        for proposal in list_proposals(
            session,
            admin.merchant_id,
            status,
            q=q,
            before_id=before_id,
            limit=limit,
        )
    ]


@router.post("/proposals/{proposal_id}/approve")
def approve(
    proposal_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> dict:
    return approve_proposal(session, admin.merchant_id, proposal_id, admin.user_id)


@router.post("/proposals/{proposal_id}/reject")
def reject(
    proposal_id: int,
    payload: RejectIn,
    admin: OwnerDep,
    session: SessionDep,
) -> dict:
    return reject_proposal(
        session, admin.merchant_id, proposal_id, admin.user_id, payload.reason
    )
