from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import Inventory, Product, StockTransfer
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.inventory_alerts import maybe_notify_low_stock
from app.services.inventory_ledger import record_movement


@dataclass(frozen=True)
class StockTransferView:
    id: int
    transfer_no: str
    sku: str
    title: str
    from_warehouse_id: str
    to_warehouse_id: str
    qty: int
    status: str
    note: str
    created_by: int | None
    created_at: datetime
    received_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str


def _view(transfer: StockTransfer) -> StockTransferView:
    return StockTransferView(
        id=transfer.id,
        transfer_no=transfer.transfer_no,
        sku=transfer.sku,
        title=transfer.title_snapshot,
        from_warehouse_id=transfer.from_warehouse_id,
        to_warehouse_id=transfer.to_warehouse_id,
        qty=transfer.qty,
        status=transfer.status,
        note=transfer.note,
        created_by=transfer.created_by,
        created_at=transfer.created_at,
        received_at=transfer.received_at,
        cancelled_at=transfer.cancelled_at,
        cancel_reason=transfer.cancel_reason,
    )


def create_stock_transfer(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    from_warehouse_id: str,
    to_warehouse_id: str,
    qty: int,
    note: str = "",
) -> StockTransferView:
    source_value = from_warehouse_id.strip()
    target_value = to_warehouse_id.strip()
    note_value = note.strip()
    if not source_value or not target_value:
        raise ValidationError("仓库编号不能为空")
    if source_value == target_value:
        raise ValidationError("调出与调入仓库不能相同")
    if qty <= 0:
        raise ValidationError("调拨数量必须大于 0")
    if len(note_value) > 500:
        raise ValidationError("调拨备注不能超过 500 个字符")
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        source = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product.id,
                Inventory.warehouse_id == source_value,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if source is None:
            raise NotFoundError("调出仓库库存不存在")
        before_qty = source.qty_available
        if qty > before_qty:
            raise ConflictError("调出仓可售库存不足")
        transfer_no = (
            f"TRF-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:8].upper()}"
        )
        transfer = StockTransfer(
            merchant_id=merchant_id,
            transfer_no=transfer_no,
            product_id=product.id,
            sku=product.sku,
            title_snapshot=product.title,
            from_warehouse_id=source.warehouse_id,
            to_warehouse_id=target_value,
            qty=qty,
            status="in_transit",
            note=note_value,
            created_by=user_id,
        )
        session.add(transfer)
        session.flush()
        record_movement(
            session,
            source,
            -qty,
            "transfer_out",
            operator_user_id=user_id,
            note=(
                f"调拨单 {transfer_no} 调出在途"
                + (f"：{note_value}" if note_value else "")
            ),
        )
        maybe_notify_low_stock(
            session,
            product,
            source.warehouse_id,
            previous_qty=before_qty,
            current_qty=source.qty_available,
        )
        append_event(
            session,
            merchant_id,
            "stock_transfer_created",
            {
                "transfer_id": transfer.id,
                "transfer_no": transfer_no,
                "sku": product.sku,
                "from_warehouse_id": source.warehouse_id,
                "to_warehouse_id": target_value,
                "qty": qty,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(transfer)
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("调拨单号冲突，请重试") from exc
    except Exception:
        session.rollback()
        raise


def receive_stock_transfer(
    session: Session,
    merchant_id: int,
    user_id: int,
    transfer_id: int,
) -> StockTransferView:
    try:
        transfer = session.execute(
            select(StockTransfer)
            .where(
                StockTransfer.id == transfer_id,
                StockTransfer.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if transfer is None:
            raise NotFoundError("调拨单不存在")
        if transfer.status != "in_transit":
            raise ConflictError("只有在途调拨单可以收货")
        target = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == transfer.product_id,
                Inventory.warehouse_id == transfer.to_warehouse_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if target is None:
            target = Inventory(
                merchant_id=merchant_id,
                product_id=transfer.product_id,
                warehouse_id=transfer.to_warehouse_id,
                warehouse_name=transfer.to_warehouse_id,
                qty_available=0,
            )
            session.add(target)
            session.flush()
        record_movement(
            session,
            target,
            transfer.qty,
            "transfer_in",
            operator_user_id=user_id,
            note=f"调拨单 {transfer.transfer_no} 调入",
        )
        transfer.status = "received"
        transfer.received_by = user_id
        transfer.received_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "stock_transfer_received",
            {
                "transfer_id": transfer.id,
                "transfer_no": transfer.transfer_no,
                "sku": transfer.sku,
                "to_warehouse_id": transfer.to_warehouse_id,
                "qty": transfer.qty,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(transfer)
    except Exception:
        session.rollback()
        raise


def cancel_stock_transfer(
    session: Session,
    merchant_id: int,
    user_id: int,
    transfer_id: int,
    reason: str,
) -> StockTransferView:
    reason_value = reason.strip()
    if not reason_value:
        raise ValidationError("请填写取消原因")
    if len(reason_value) > 500:
        raise ValidationError("取消原因不能超过 500 个字符")
    try:
        transfer = session.execute(
            select(StockTransfer)
            .where(
                StockTransfer.id == transfer_id,
                StockTransfer.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if transfer is None:
            raise NotFoundError("调拨单不存在")
        if transfer.status != "in_transit":
            raise ConflictError("只有在途调拨单可以取消")
        source = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == transfer.product_id,
                Inventory.warehouse_id == transfer.from_warehouse_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if source is None:
            raise ConflictError("调出仓库存行不存在，无法回补")
        record_movement(
            session,
            source,
            transfer.qty,
            "transfer_cancel_in",
            operator_user_id=user_id,
            note=f"调拨单 {transfer.transfer_no} 取消回补",
        )
        transfer.status = "cancelled"
        transfer.cancelled_by = user_id
        transfer.cancelled_at = datetime.now(UTC)
        transfer.cancel_reason = reason_value
        append_event(
            session,
            merchant_id,
            "stock_transfer_cancelled",
            {
                "transfer_id": transfer.id,
                "transfer_no": transfer.transfer_no,
                "sku": transfer.sku,
                "from_warehouse_id": transfer.from_warehouse_id,
                "qty": transfer.qty,
                "reason": reason_value,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(transfer)
    except Exception:
        session.rollback()
        raise


def list_stock_transfers(
    session: Session,
    merchant_id: int,
    status: str = "all",
    limit: int = 100,
) -> list[StockTransferView]:
    stmt = select(StockTransfer).where(
        StockTransfer.merchant_id == merchant_id
    )
    if status != "all":
        if status not in ("in_transit", "received", "cancelled"):
            raise ValidationError("未知调拨状态")
        stmt = stmt.where(StockTransfer.status == status)
    rows = session.execute(
        stmt.order_by(StockTransfer.id.desc()).limit(limit)
    ).scalars().all()
    return [_view(transfer) for transfer in rows]