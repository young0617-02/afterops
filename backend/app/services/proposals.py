from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    Customer,
    Inventory,
    InventoryReservation,
    InvoiceRequest,
    Order,
    OrderItem,
    Payment,
    Refund,
    Shipment,
)
from app.domain.money import format_cents
from app.services.attachments import validate_attachment_ids
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.fulfillment import (
    cancelable_quantities,
    refresh_order_status,
    shipments_for_order,
)
from app.services.inventory_ledger import record_movement
from app.services.notifications import notify_customer, notify_staff
from app.settings import settings

ACTION_LABELS = {
    "refund": "退款",
    "compensation": "补偿",
    "address_change": "改地址",
}


@dataclass(frozen=True)
class ProposalResult:
    proposal_id: int
    action_type: str
    status: str
    risk_level: str
    order_no: str
    amount_cents: int | None

    def to_json(self) -> dict:
        return {
            "id": self.proposal_id,
            "action_type": self.action_type,
            "status": self.status,
            "risk_level": self.risk_level,
            "order_no": self.order_no,
            "amount_cents": self.amount_cents,
        }


def _order_for_customer(
    session: Session, customer: Customer, order_no: str
) -> Order:
    order = session.execute(
        select(Order).where(
            Order.order_no == order_no,
            Order.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    return order


def _create_case(
    session: Session,
    merchant_id: int,
    order: Order,
    case_type: str,
    reason: str,
    amount_cents: int | None,
) -> AfterSalesCase:
    case = AfterSalesCase(
        merchant_id=merchant_id,
        order_id=order.id,
        type=case_type,
        status="requested",
        reason=reason,
        amount_cents=amount_cents,
    )
    session.add(case)
    session.flush()
    return case


def _create_proposal(
    session: Session,
    customer: Customer,
    order: Order,
    action_type: str,
    risk_level: str,
    payload: dict,
    case: AfterSalesCase,
    conversation_id: int | None,
    created_by: str = "ai",
) -> ProposalResult:
    proposal = ActionProposal(
        merchant_id=customer.merchant_id,
        conversation_id=conversation_id,
        after_sales_case_id=case.id,
        action_type=action_type,
        payload_json=payload,
        risk_level=risk_level,
        status="pending",
        idempotency_key=f"{created_by}:{action_type}:{uuid4().hex}",
        created_by=created_by,
    )
    session.add(proposal)
    session.flush()
    append_event(
        session,
        customer.merchant_id,
        "proposal_created",
        {
            "proposal_id": proposal.id,
            "action_type": action_type,
            "order_no": order.order_no,
            "amount_cents": payload.get("amount_cents"),
            "created_by": created_by,
        },
        conversation_id=conversation_id,
        proposal_id=proposal.id,
    )
    amount = payload.get("amount_cents")
    notify_staff(
        session,
        customer.merchant_id,
        notification_type="proposal_created",
        title=f"新的{ACTION_LABELS.get(action_type, action_type)}提案 #{proposal.id}",
        body=f"订单 {order.order_no}"
        + (f" · {format_cents(int(amount))} 元" if amount else "")
        + " · 等待审批",
        link="/admin",
    )
    return ProposalResult(
        proposal_id=proposal.id,
        action_type=action_type,
        status=proposal.status,
        risk_level=risk_level,
        order_no=order.order_no,
        amount_cents=payload.get("amount_cents"),
    )


def refunded_total(session: Session, order_id: int) -> int:
    value = session.execute(
        select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
            Refund.order_id == order_id,
            Refund.kind == "refund",
            Refund.status == "executed",
        )
    ).scalar()
    return int(value or 0)


def create_refund_proposal(
    session: Session,
    customer: Customer,
    order_no: str,
    amount_cents: int | None,
    reason: str,
    conversation_id: int | None = None,
    created_by: str = "ai",
    cancel_request: bool = False,
    cancel_items: list[dict] | None = None,
    cancel_scope: str = "full",
) -> ProposalResult:
    order = _order_for_customer(session, customer, order_no)
    if order.payment_status not in ("paid", "partially_refunded"):
        raise ConflictError("订单未支付，暂不能申请退款")
    remaining = order.payable_cents - refunded_total(session, order.id)
    if remaining <= 0:
        raise ConflictError("该订单已无可退金额")
    amount = remaining if amount_cents is None else amount_cents
    if amount <= 0:
        raise ValidationError("退款金额必须大于 0")
    if amount > remaining:
        raise ConflictError(f"退款金额超过可退上限 {format_cents(remaining)}")
    reason_text = reason.strip() or "顾客申请退款"
    case = _create_case(
        session, customer.merchant_id, order, "refund", reason_text, amount
    )
    return _create_proposal(
        session,
        customer,
        order,
        "refund",
        "L2",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "amount_cents": amount,
            "reason": reason_text,
            **({"cancel_request": True} if cancel_request else {}),
            **({"cancel_scope": cancel_scope} if cancel_request else {}),
            **({"cancel_items": cancel_items or []} if cancel_request else {}),
        },
        case,
        conversation_id,
        created_by=created_by,
    )


@dataclass(frozen=True)
class CancelRequestResult:
    order_no: str
    proposal_id: int
    amount_cents: int
    status: str
    scope: str
    items: list[dict]


def request_order_cancellation(
    session: Session,
    customer: Customer,
    order_no: str,
    items: list[tuple[str, int]] | None = None,
) -> CancelRequestResult:
    try:
        order = session.execute(
            select(Order)
            .where(
                Order.order_no == order_no,
                Order.merchant_id == customer.merchant_id,
                Order.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("订单不存在")
        order_items = list(
            session.execute(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.id)
                .with_for_update()
            ).scalars().all()
        )
        partial = bool(items)
        if partial:
            if order.payment_status not in ("paid", "partially_refunded"):
                raise ConflictError("当前支付状态不能取消未发货商品")
            if order.status not in (
                "paid",
                "fulfilling",
                "shipped",
                "partially_refunded",
            ):
                raise ConflictError("当前订单状态不能取消未发货商品")
        else:
            if order.status != "paid" or order.payment_status != "paid":
                raise ConflictError("只有已支付且未发货的订单可以申请取消")
            if order.fulfillment_status != "unfulfilled":
                raise ConflictError("订单已进入履约，不能申请取消")
            shipment = session.execute(
                select(Shipment.id)
                .where(Shipment.order_id == order.id)
                .limit(1)
            ).scalar_one_or_none()
            if shipment is not None:
                raise ConflictError("订单已创建运单，不能申请取消")
        if order.cancel_requested_at is not None:
            raise ConflictError("订单已有取消申请待审核")
        pending = session.execute(
            select(ActionProposal.id)
            .join(
                AfterSalesCase,
                AfterSalesCase.id == ActionProposal.after_sales_case_id,
            )
            .where(
                AfterSalesCase.order_id == order.id,
                ActionProposal.status == "pending",
            )
        ).scalar_one_or_none()
        if pending is not None:
            raise ConflictError("订单已有待审售后申请")
        remaining = order.payable_cents - refunded_total(session, order.id)
        if remaining <= 0:
            raise ConflictError("该订单已无可退金额")

        requested: dict[int, int] = {}
        selected_items: list[dict] = []
        scope = "full"
        amount = remaining
        if partial:
            shipments = shipments_for_order(
                session, order.id, for_update=True
            )
            cancelable = cancelable_quantities(session, order_items, shipments)
            by_sku = {item.sku: item for item in order_items}
            for raw_sku, raw_qty in items or []:
                sku_value = raw_sku.strip().upper()
                item = by_sku.get(sku_value)
                if item is None:
                    raise ValidationError("取消商品不属于该订单")
                if raw_qty < 1:
                    raise ValidationError("取消数量必须大于 0")
                requested[item.id] = requested.get(item.id, 0) + raw_qty
                if requested[item.id] > cancelable[item.id]:
                    raise ConflictError(
                        f"{item.sku} 最多还能取消 {cancelable[item.id]} 件"
                    )
            if not requested:
                raise ValidationError("请选择要取消的商品")
            selected_items = [
                {
                    "order_item_id": item.id,
                    "sku": item.sku,
                    "qty": requested[item.id],
                }
                for item in order_items
                if item.id in requested
            ]
            full_after = all(
                item.cancelled_qty + requested.get(item.id, 0) >= item.qty
                for item in order_items
            )
            if full_after:
                scope = "full"
                amount = remaining
            else:
                item_total = sum(
                    item.unit_price_cents * item.qty for item in order_items
                )
                selected_subtotal = sum(
                    item.unit_price_cents * requested[item.id]
                    for item in order_items
                    if item.id in requested
                )
                discount_share = (
                    order.discount_cents * selected_subtotal // item_total
                    if item_total > 0
                    else 0
                )
                amount = min(
                    max(selected_subtotal - discount_share, 0), remaining
                )
                scope = "partial"
            if amount <= 0:
                raise ValidationError("取消商品的可退金额为 0")

        reason = (
            "顾客申请取消未发货商品："
            + "、".join(
                f"{item['sku']} × {item['qty']}" for item in selected_items
            )
            if selected_items
            else "顾客申请取消未发货订单"
        )
        proposal = create_refund_proposal(
            session,
            customer,
            order.order_no,
            amount,
            reason,
            created_by="customer",
            cancel_request=True,
            cancel_items=selected_items,
            cancel_scope=scope,
        )
        order.cancel_requested_at = datetime.now(UTC)
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="order_cancel_requested",
            title=f"取消申请 {order.order_no}",
            body=(
                f"顾客申请取消未发货商品（{len(selected_items)} 项），"
                "请审批；审批前已暂停发货。"
                if selected_items
                else "顾客申请取消已支付未发货订单，请审批；审批前已暂停发货。"
            ),
            link="/admin",
        )
        append_event(
            session,
            customer.merchant_id,
            "order_cancel_requested",
            {
                "order_no": order.order_no,
                "proposal_id": proposal.proposal_id,
                "amount_cents": amount,
                "scope": scope,
                "items": selected_items,
            },
        )
        session.commit()
        return CancelRequestResult(
            order_no=order.order_no,
            proposal_id=proposal.proposal_id,
            amount_cents=amount,
            status="requested",
            scope=scope,
            items=selected_items,
        )
    except Exception:
        session.rollback()
        raise


def _restore_cancelled_inventory(
    session: Session, order: Order
) -> list[dict]:
    reservations = session.execute(
        select(InventoryReservation)
        .where(
            InventoryReservation.order_id == order.id,
            InventoryReservation.status == "committed",
        )
        .order_by(InventoryReservation.product_id)
        .with_for_update()
    ).scalars().all()
    lines: list[dict] = []
    for reservation in reservations:
        inventory = session.execute(
            select(Inventory)
            .where(
                Inventory.merchant_id == order.merchant_id,
                Inventory.product_id == reservation.product_id,
                Inventory.warehouse_id == reservation.warehouse_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            continue
        record_movement(
            session,
            inventory,
            reservation.qty,
            "order_cancelled",
            order_id=order.id,
            note="已支付取消回补",
        )
        reservation.status = "released"
        reservation.expires_at = None
        lines.append(
            {
                "product_id": reservation.product_id,
                "warehouse_id": reservation.warehouse_id,
                "qty": reservation.qty,
            }
        )
    if not reservations:
        items = session.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        ).scalars().all()
        for item in items:
            restore_qty = item.qty - item.cancelled_qty
            if restore_qty <= 0:
                continue
            inventory = session.execute(
                select(Inventory)
                .where(
                    Inventory.merchant_id == order.merchant_id,
                    Inventory.product_id == item.product_id,
                )
                .order_by(Inventory.warehouse_id)
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()
            if inventory is None:
                continue
            record_movement(
                session,
                inventory,
                restore_qty,
                "order_cancelled",
                order_id=order.id,
                note="已支付取消回补",
            )
            lines.append(
                {
                    "product_id": item.product_id,
                    "warehouse_id": inventory.warehouse_id,
                    "qty": restore_qty,
                }
            )
    return lines


def create_staff_action_proposal(
    session: Session,
    merchant_id: int,
    action_type: str,
    order_no: str,
    amount_cents: int | None = None,
    reason: str = "",
    new_address_text: str | None = None,
) -> ProposalResult:
    row = session.execute(
        select(Order, Customer)
        .join(Customer, Customer.id == Order.customer_id)
        .where(
            Order.merchant_id == merchant_id,
            Order.order_no == order_no,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("订单不存在")
    _order, customer = row
    try:
        if action_type == "refund":
            result = create_refund_proposal(
                session,
                customer,
                order_no,
                amount_cents,
                reason or "店主代顾客发起退款",
                created_by="staff",
            )
        elif action_type == "compensation":
            result = create_compensation_proposal(
                session,
                customer,
                order_no,
                amount_cents,
                reason or "店主代顾客发起补偿",
                created_by="staff",
            )
        elif action_type == "address_change":
            if not new_address_text:
                raise ValidationError("请提供新地址")
            result = create_address_change_proposal(
                session,
                customer,
                order_no,
                new_address_text,
                created_by="staff",
            )
        else:
            raise ValidationError(f"不支持的人工售后类型 {action_type}")
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def create_compensation_proposal(
    session: Session,
    customer: Customer,
    order_no: str,
    amount_cents: int | None,
    reason: str,
    conversation_id: int | None = None,
    created_by: str = "ai",
) -> ProposalResult:
    order = _order_for_customer(session, customer, order_no)
    if order.payment_status not in ("paid", "partially_refunded"):
        raise ConflictError("订单未支付，暂不能申请补偿")
    if amount_cents is None or amount_cents <= 0:
        raise ValidationError("请提供补偿金额")
    if amount_cents > settings.compensation_max_cents:
        raise ConflictError(
            f"补偿金额超过上限 {format_cents(settings.compensation_max_cents)}"
        )
    reason_text = reason.strip() or "顾客申请补偿"
    case = _create_case(
        session, customer.merchant_id, order, "compensation", reason_text, amount_cents
    )
    return _create_proposal(
        session,
        customer,
        order,
        "compensation",
        "L2",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "amount_cents": amount_cents,
            "reason": reason_text,
        },
        case,
        conversation_id,
        created_by=created_by,
    )


def create_address_change_proposal(
    session: Session,
    customer: Customer,
    order_no: str,
    new_address_text: str,
    conversation_id: int | None = None,
    created_by: str = "ai",
) -> ProposalResult:
    order = _order_for_customer(session, customer, order_no)
    if order.status not in ("pending_payment", "paid"):
        raise ConflictError("订单已进入履约，不能直接改地址，请转人工")
    new_address = new_address_text.strip()
    if not new_address:
        raise ValidationError("请提供新地址")
    if len(new_address) > 255:
        raise ValidationError("新地址过长，请精简到 255 字以内")
    case = _create_case(
        session, customer.merchant_id, order, "address_change", "顾客申请修改收货地址", None
    )
    return _create_proposal(
        session,
        customer,
        order,
        "address_change",
        "L1",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "new_address_text": new_address,
            "current_address_detail": order.address_detail,
        },
        case,
        conversation_id,
        created_by=created_by,
    )


def list_proposals(
    session: Session,
    merchant_id: int,
    status: str | None = None,
    *,
    q: str | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[ActionProposal]:
    stmt = select(ActionProposal).where(
        ActionProposal.merchant_id == merchant_id
    )
    if status:
        stmt = stmt.where(ActionProposal.status == status)
    if q and q.strip():
        value = q.strip()
        if value.isdigit():
            stmt = stmt.where(ActionProposal.id == int(value))
        else:
            escaped = (
                value.replace("\\", "\\\\")
                .replace("%", r"\%")
                .replace("_", r"\_")
            )
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                cast(ActionProposal.payload_json, String).ilike(
                    pattern, escape="\\"
                )
            )
    if before_id is not None:
        stmt = stmt.where(ActionProposal.id < before_id)
    return session.execute(
        stmt.order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc())
        .limit(limit)
    ).scalars().all()


def proposal_to_dict(proposal: ActionProposal) -> dict:
    payload = proposal.payload_json or {}
    return {
        "id": proposal.id,
        "action_type": proposal.action_type,
        "status": proposal.status,
        "risk_level": proposal.risk_level,
        "order_no": payload.get("order_no"),
        "amount_cents": payload.get("amount_cents"),
        "amount_display": format_cents(payload["amount_cents"])
        if payload.get("amount_cents") is not None
        else None,
        "created_by": proposal.created_by,
        "created_at": proposal.created_at.isoformat(),
        "approved_by": proposal.approved_by,
        "commerce_ref": proposal.commerce_ref,
        "attachment_ids": payload.get("attachment_ids") or [],
        "reason": payload.get("reason") or "",
        "cancel_scope": payload.get("cancel_scope"),
        "cancel_items": payload.get("cancel_items") or [],
    }


def _restore_partial_inventory(
    session: Session,
    order: Order,
    cancelled: list[tuple[OrderItem, int]],
) -> list[dict]:
    reservations = session.execute(
        select(InventoryReservation)
        .where(
            InventoryReservation.order_id == order.id,
            InventoryReservation.status == "committed",
        )
        .with_for_update()
    ).scalars().all()
    reservation_by_product = {
        reservation.product_id: reservation for reservation in reservations
    }
    lines: list[dict] = []
    for item, qty in cancelled:
        reservation = reservation_by_product.get(item.product_id)
        warehouse_id = (
            reservation.warehouse_id
            if reservation is not None
            else "default"
        )
        inventory = session.execute(
            select(Inventory)
            .where(
                Inventory.merchant_id == order.merchant_id,
                Inventory.product_id == item.product_id,
                Inventory.warehouse_id == warehouse_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            inventory = session.execute(
                select(Inventory)
                .where(
                    Inventory.merchant_id == order.merchant_id,
                    Inventory.product_id == item.product_id,
                )
                .order_by(Inventory.warehouse_id)
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()
        if inventory is None:
            continue
        restore_qty = min(qty, reservation.qty) if reservation is not None else qty
        if restore_qty <= 0:
            continue
        record_movement(
            session,
            inventory,
            restore_qty,
            "order_cancelled",
            order_id=order.id,
            note="部分取消回补",
        )
        if reservation is not None:
            reservation.qty -= restore_qty
            if reservation.qty == 0:
                session.delete(reservation)
                reservation_by_product.pop(item.product_id, None)
        lines.append(
            {
                "order_item_id": item.id,
                "sku": item.sku,
                "product_id": item.product_id,
                "warehouse_id": inventory.warehouse_id,
                "qty": restore_qty,
            }
        )
    return lines


def _apply_cancelled_items(
    session: Session,
    order: Order,
    requested_items: list[dict],
) -> tuple[list[dict], bool]:
    order_items = list(
        session.execute(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.id)
            .with_for_update()
        ).scalars().all()
    )
    by_id = {item.id: item for item in order_items}
    shipments = shipments_for_order(session, order.id, for_update=True)
    cancelable = cancelable_quantities(session, order_items, shipments)
    requested: dict[int, int] = {}
    for raw in requested_items:
        item = by_id.get(int(raw.get("order_item_id") or 0))
        if item is None:
            raise ConflictError("取消商品不属于该订单")
        qty = int(raw.get("qty") or 0)
        if qty < 1:
            raise ConflictError("取消数量不合法")
        requested[item.id] = requested.get(item.id, 0) + qty
        if requested[item.id] > cancelable[item.id]:
            raise ConflictError(
                f"{item.sku} 最多还能取消 {cancelable[item.id]} 件"
            )
    if not requested:
        raise ConflictError("取消申请缺少商品")
    cancelled = [
        (item, requested[item.id])
        for item in order_items
        if item.id in requested
    ]
    lines = _restore_partial_inventory(session, order, cancelled)
    for item, qty in cancelled:
        item.cancelled_qty += qty
    all_cancelled = all(
        item.cancelled_qty >= item.qty for item in order_items
    )
    return lines, all_cancelled


def approve_proposal(
    session: Session,
    merchant_id: int,
    proposal_id: int,
    approver_id: int,
) -> dict:
    try:
        proposal = session.execute(
            select(ActionProposal)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if proposal is None:
            raise NotFoundError("提案不存在")
        if proposal.status != "pending":
            raise ConflictError(f"提案状态 {proposal.status} 不能审批")

        payload = dict(proposal.payload_json or {})
        case = session.execute(
            select(AfterSalesCase)
            .where(AfterSalesCase.id == proposal.after_sales_case_id)
            .with_for_update()
        ).scalar_one_or_none()
        order = session.execute(
            select(Order).where(Order.id == payload.get("order_id")).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise ConflictError("提案关联订单不存在")
        cancel_request = bool(payload.get("cancel_request"))
        cancel_items_payload = list(payload.get("cancel_items") or [])
        partial_cancel = cancel_request and bool(cancel_items_payload)
        if cancel_request and not partial_cancel:
            if order.fulfillment_status != "unfulfilled":
                raise ConflictError("订单已进入履约，不能按取消处理")
            shipment_exists = session.execute(
                select(Shipment.id)
                .where(Shipment.order_id == order.id)
                .limit(1)
            ).scalar_one_or_none()
            if shipment_exists is not None:
                raise ConflictError("订单已创建运单，不能按取消处理")

        now = datetime.now(UTC)
        proposal.status = "approved"
        proposal.approved_by = approver_id
        proposal.approved_at = now
        append_event(
            session,
            merchant_id,
            "proposal_approved",
            {
                "proposal_id": proposal.id,
                "action_type": proposal.action_type,
                "order_no": order.order_no,
                "approved_by": approver_id,
            },
            conversation_id=proposal.conversation_id,
            proposal_id=proposal.id,
        )

        commerce_ref: str | None = None
        if proposal.action_type in ("refund", "compensation"):
            payment = session.execute(
                select(Payment)
                .where(Payment.order_id == order.id)
                .order_by(Payment.id.desc())
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()
            if payment is None or payment.status not in ("paid", "partially_refunded"):
                raise ConflictError("支付状态不允许执行该提案")
            amount = int(payload.get("amount_cents") or 0)
            if proposal.action_type == "refund":
                remaining = order.payable_cents - refunded_total(session, order.id)
                if amount <= 0 or amount > remaining:
                    raise ConflictError("退款金额超过可退上限")
                kind = "refund"
                provider_refund_id = f"MOCK-REFUND-{uuid4().hex.upper()}"
            else:
                if amount <= 0 or amount > settings.compensation_max_cents:
                    raise ConflictError("补偿金额不合法")
                kind = "compensation"
                provider_refund_id = f"MOCK-COMP-{uuid4().hex.upper()}"

            refund = Refund(
                merchant_id=merchant_id,
                order_id=order.id,
                payment_id=payment.id,
                after_sales_case_id=case.id if case else None,
                proposal_id=proposal.id,
                kind=kind,
                amount_cents=amount,
                status="executed",
                provider_refund_id=provider_refund_id,
                settlement_status="processing",
                settlement_attempts=1,
                executed_at=now,
            )
            session.add(refund)
            session.flush()
            commerce_ref = provider_refund_id

            if kind == "refund":
                total = refunded_total(session, order.id)
                new_status = (
                    "refunded" if total >= payment.amount_cents else "partially_refunded"
                )
                payment.status = new_status
                order.payment_status = new_status
                if partial_cancel:
                    restocked, all_cancelled = _apply_cancelled_items(
                        session, order, cancel_items_payload
                    )
                    order.cancel_requested_at = None
                    if all_cancelled:
                        order.status = new_status
                    else:
                        order_items = list(
                            session.execute(
                                select(OrderItem).where(
                                    OrderItem.order_id == order.id
                                )
                            ).scalars().all()
                        )
                        refresh_order_status(
                            session,
                            order,
                            order_items,
                            shipments_for_order(session, order.id),
                        )
                    append_event(
                        session,
                        merchant_id,
                        "order_partially_cancelled",
                        {
                            "order_no": order.order_no,
                            "proposal_id": proposal.id,
                            "items": cancel_items_payload,
                            "lines": restocked,
                            "all_cancelled": all_cancelled,
                            "amount_cents": amount,
                        },
                    )
                else:
                    order.status = new_status
                    if cancel_request:
                        restocked = _restore_cancelled_inventory(session, order)
                        order.cancel_requested_at = None
                        append_event(
                            session,
                            merchant_id,
                            "order_cancel_restocked",
                            {
                                "order_no": order.order_no,
                                "proposal_id": proposal.id,
                                "lines": restocked,
                            },
                        )
                invoice = session.execute(
                    select(InvoiceRequest)
                    .where(
                        InvoiceRequest.order_id == order.id,
                        InvoiceRequest.status.in_(("requested", "issued")),
                    )
                    .order_by(InvoiceRequest.id.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if invoice is not None:
                    net_paid = max(order.payable_cents - total, 0)
                    previous_net = max(
                        order.payable_cents - (total - amount), 0
                    )
                    if (
                        invoice.amount_cents != net_paid
                        and invoice.amount_cents == previous_net
                    ):
                        append_event(
                            session,
                            merchant_id,
                            "invoice_adjustment_required",
                            {
                                "order_no": order.order_no,
                                "invoice_id": invoice.id,
                                "invoice_status": invoice.status,
                                "invoice_amount_cents": invoice.amount_cents,
                                "net_paid_cents": net_paid,
                                "proposal_id": proposal.id,
                            },
                        )
                        notify_staff(
                            session,
                            merchant_id,
                            notification_type="invoice_adjustment_required",
                            title=f"发票需重开 #{invoice.id}",
                            body=(
                                f"订单 {order.order_no} · 发票金额 "
                                f"{format_cents(invoice.amount_cents)} 元 · "
                                f"当前净支付 {format_cents(net_paid)} 元，"
                                "请作废后重开"
                            ),
                            link="/admin",
                        )
                        notify_customer(
                            session,
                            merchant_id,
                            order.customer_id,
                            notification_type="invoice_adjustment_required",
                            title="发票金额需调整",
                            body=(
                                f"订单 {order.order_no} 退款后净支付 "
                                f"{format_cents(net_paid)} 元，与发票金额 "
                                f"{format_cents(invoice.amount_cents)} 元不一致，"
                                "需由商家处理发票重开。"
                            ),
                            link="/orders",
                        )
        elif proposal.action_type == "address_change":
            if order.status not in ("pending_payment", "paid"):
                raise ConflictError("订单已进入履约，不能改地址")
            new_address = str(payload.get("new_address_text", "")).strip()
            if not new_address:
                raise ConflictError("提案缺少新地址")
            order.address_detail = new_address[:255]
            commerce_ref = order.order_no
        else:
            raise ConflictError(f"不支持的提案类型 {proposal.action_type}")

        if case is not None:
            case.status = "executed"
        proposal.status = "executed"
        proposal.executed_at = now
        proposal.commerce_ref = commerce_ref
        append_event(
            session,
            merchant_id,
            "proposal_executed",
            {
                "proposal_id": proposal.id,
                "action_type": proposal.action_type,
                "order_no": order.order_no,
                "commerce_ref": commerce_ref,
                "amount_cents": payload.get("amount_cents"),
            },
            conversation_id=proposal.conversation_id,
            proposal_id=proposal.id,
        )
        amount = payload.get("amount_cents")
        if proposal.action_type == "address_change":
            notification_title = "收货地址已更新"
        elif proposal.action_type == "compensation":
            notification_title = "补偿已执行"
        else:
            notification_title = "退款已执行"
        notify_customer(
            session,
            merchant_id,
            order.customer_id,
            notification_type="proposal_executed",
            title=notification_title,
            body=f"订单 {order.order_no}"
            + (f" · {format_cents(int(amount))} 元" if amount else "")
            + (f" · 处理引用 {commerce_ref}" if commerce_ref else ""),
            link="/after-sales",
        )
        session.commit()
        return {
            "id": proposal.id,
            "action_type": proposal.action_type,
            "status": proposal.status,
            "order_no": order.order_no,
            "commerce_ref": commerce_ref,
            "amount_cents": payload.get("amount_cents"),
        }
    except Exception:
        session.rollback()
        raise


def withdraw_proposal(
    session: Session, customer: Customer, proposal_id: int
) -> dict:
    try:
        row = session.execute(
            select(ActionProposal, AfterSalesCase, Order)
            .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
            .join(Order, Order.id == AfterSalesCase.order_id)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.merchant_id == customer.merchant_id,
                Order.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("售后申请不存在")
        proposal, case, order = row
        if proposal.status != "pending":
            raise ConflictError("只有待审核申请可以撤回")
        proposal.status = "withdrawn"
        proposal.error = "顾客撤回"
        case.status = "withdrawn"
        append_event(
            session,
            customer.merchant_id,
            "proposal_withdrawn",
            {
                "proposal_id": proposal.id,
                "action_type": proposal.action_type,
                "order_no": order.order_no,
            },
            conversation_id=proposal.conversation_id,
            proposal_id=proposal.id,
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="proposal_withdrawn",
            title=(
                f"顾客撤回{ACTION_LABELS.get(proposal.action_type, proposal.action_type)}"
                f"申请 #{proposal.id}"
            ),
            body=f"订单 {order.order_no} · 申请已撤回",
            link="/admin",
        )
        session.commit()
        return {"id": proposal.id, "status": proposal.status}
    except Exception:
        session.rollback()
        raise


def reject_proposal(
    session: Session,
    merchant_id: int,
    proposal_id: int,
    approver_id: int,
    reason: str,
) -> dict:
    try:
        proposal = session.execute(
            select(ActionProposal)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if proposal is None:
            raise NotFoundError("提案不存在")
        if proposal.status != "pending":
            raise ConflictError(f"提案状态 {proposal.status} 不能驳回")

        case = session.execute(
            select(AfterSalesCase)
            .where(AfterSalesCase.id == proposal.after_sales_case_id)
            .with_for_update()
        ).scalar_one_or_none()
        if case is not None:
            case.status = "rejected"
        proposal.status = "rejected"
        proposal.approved_by = approver_id
        proposal.approved_at = datetime.now(UTC)
        proposal.error = reason.strip() or "人工驳回"
        append_event(
            session,
            merchant_id,
            "proposal_rejected",
            {
                "proposal_id": proposal.id,
                "action_type": proposal.action_type,
                "approved_by": approver_id,
                "reason": proposal.error,
            },
            conversation_id=proposal.conversation_id,
            proposal_id=proposal.id,
        )
        order = (
            session.get(Order, case.order_id) if case is not None else None
        )
        if order is not None:
            if proposal.payload_json.get("cancel_request"):
                order.cancel_requested_at = None
            notify_customer(
                session,
                merchant_id,
                order.customer_id,
                notification_type="proposal_rejected",
                title=f"{ACTION_LABELS.get(proposal.action_type, proposal.action_type)}申请未通过",
                body=(
                    f"订单 {order.order_no} · 原因：{proposal.error}"
                ),
                link="/after-sales",
            )
        session.commit()
        return {
            "id": proposal.id,
            "action_type": proposal.action_type,
            "status": proposal.status,
            "reason": proposal.error,
        }
    except Exception:
        session.rollback()
        raise


def add_proposal_attachments(
    session: Session,
    customer: Customer,
    proposal_id: int,
    attachment_ids: list[int],
) -> dict:
    try:
        row = session.execute(
            select(ActionProposal, AfterSalesCase, Order)
            .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
            .join(Order, Order.id == AfterSalesCase.order_id)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.merchant_id == customer.merchant_id,
                Order.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("售后申请不存在")
        proposal, _case, order = row
        if proposal.status != "pending":
            raise ConflictError("只有待审核申请可以补充凭证")
        payload = dict(proposal.payload_json or {})
        existing = list(payload.get("attachment_ids") or [])
        merged = list(dict.fromkeys(existing + attachment_ids))
        if len(merged) > 3:
            raise ValidationError("最多上传 3 张图片")
        valid_ids = validate_attachment_ids(session, customer, merged)
        payload["attachment_ids"] = valid_ids
        proposal.payload_json = payload
        append_event(
            session,
            customer.merchant_id,
            "proposal_evidence_added",
            {
                "proposal_id": proposal.id,
                "order_no": order.order_no,
                "attachment_ids": valid_ids,
            },
            conversation_id=proposal.conversation_id,
            proposal_id=proposal.id,
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="proposal_evidence_added",
            title=f"提案补充凭证 #{proposal.id}",
            body=f"订单 {order.order_no} · {len(valid_ids)} 张图片",
            link="/admin",
        )
        session.commit()
        return {"id": proposal.id, "attachment_ids": valid_ids}
    except Exception:
        session.rollback()
        raise
