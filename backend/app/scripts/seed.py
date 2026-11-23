"""Seed the AfterOps Postgres database from the existing synthetic SQLite shop.

Run from backend/:
    uv run python -m app.scripts.seed --reset

The script is idempotent: without --reset it refuses to duplicate the demo merchant.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    AuditEvent,
    Cart,
    CartItem,
    Conversation,
    Customer,
    HandoffTicket,
    IdempotencyKey,
    Inventory,
    InventoryReservation,
    Merchant,
    Message,
    Notification,
    Order,
    OrderItem,
    Outbox,
    Payment,
    PolicyEntry,
    Product,
    Refund,
    Shipment,
    ShipmentEvent,
    User,
)
from app.services.passwords import hash_password
from app.settings import settings

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE = REPO_ROOT / "data" / "synthetic_shop" / "shop.db"


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql).fetchall()]


def _order_state(status: str, refund_status: str, shipment_status: str | None) -> tuple[str, str, str]:
    mapping = {
        "待付款": "pending_payment",
        "已付款待发货": "paid",
        "已发货": "shipped",
        "运输中": "shipped",
        "已签收": "delivered",
        "退款申请中": "refunding",
        "退款成功": "refunded",
        "已取消": "cancelled",
    }
    if status not in mapping:
        raise ValueError(f"未知订单状态：{status}")
    order_status = mapping[status]
    if refund_status == "成功" or status == "退款成功":
        order_status = "refunded"
        payment_status = "refunded"
    elif status in ("待付款", "已取消"):
        payment_status = "unpaid"
    else:
        payment_status = "paid"
    fulfillment = {
        "delivered": "delivered",
        "in_transit": "shipped",
        "out_for_delivery": "shipped",
        "exception": "shipped",
    }.get(shipment_status or "", "unfulfilled")
    return order_status, payment_status, fulfillment


def _shipment_state(status: str) -> str:
    mapping = {
        "待揽收": "pending",
        "运输中": "in_transit",
        "派送中": "out_for_delivery",
        "已签收": "delivered",
        "异常件": "exception",
    }
    if status not in mapping:
        raise ValueError(f"未知物流状态：{status}")
    return mapping[status]


def _refund_case_state(refund_status: str) -> str | None:
    return {
        "申请中": "requested",
        "已受理待核": "reviewing",
        "成功": "executed",
        "驳回": "rejected",
    }.get(refund_status)


def reset_demo(session: Session, merchant: Merchant) -> None:
    mid = merchant.id
    conversation_ids = select(Conversation.id).where(Conversation.merchant_id == mid)
    shipment_ids = select(Shipment.id).where(Shipment.merchant_id == mid)
    order_ids = select(Order.id).where(Order.merchant_id == mid)
    cart_ids = select(Cart.id).where(Cart.merchant_id == mid)

    session.execute(delete(AuditEvent).where(AuditEvent.merchant_id == mid))
    session.execute(delete(Outbox).where(Outbox.merchant_id == mid))
    session.execute(delete(IdempotencyKey).where(IdempotencyKey.scope.like("seed:%")))
    session.execute(delete(Refund).where(Refund.merchant_id == mid))
    session.execute(delete(ActionProposal).where(ActionProposal.merchant_id == mid))
    session.execute(delete(AfterSalesCase).where(AfterSalesCase.merchant_id == mid))
    session.execute(delete(HandoffTicket).where(HandoffTicket.merchant_id == mid))
    session.execute(delete(Notification).where(Notification.merchant_id == mid))
    session.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
    session.execute(delete(Conversation).where(Conversation.merchant_id == mid))
    session.execute(delete(ShipmentEvent).where(ShipmentEvent.shipment_id.in_(shipment_ids)))
    session.execute(delete(Shipment).where(Shipment.merchant_id == mid))
    session.execute(delete(InventoryReservation).where(InventoryReservation.order_id.in_(order_ids)))
    session.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
    session.execute(delete(Payment).where(Payment.order_id.in_(order_ids)))
    session.execute(delete(Order).where(Order.merchant_id == mid))
    session.execute(delete(CartItem).where(CartItem.cart_id.in_(cart_ids)))
    session.execute(delete(Cart).where(Cart.merchant_id == mid))
    session.execute(delete(Inventory).where(Inventory.merchant_id == mid))
    session.execute(delete(Product).where(Product.merchant_id == mid))
    session.execute(delete(PolicyEntry).where(PolicyEntry.merchant_id == mid))
    session.execute(delete(Customer).where(Customer.merchant_id == mid))
    session.execute(delete(User).where(User.merchant_id == mid))
    session.delete(merchant)
    session.commit()


def seed(sqlite_path: Path, merchant_name: str, reset: bool) -> dict[str, int]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite 源数据不存在：{sqlite_path}")

    src = sqlite3.connect(sqlite_path)
    try:
        products = _rows(src, "SELECT * FROM products ORDER BY sku")
        inventory = _rows(src, "SELECT * FROM inventory ORDER BY sku, region")
        orders = _rows(
            src,
            """
            SELECT o.*, s.item_total_cents, s.payable_cents, r.refundable_max_cents
            FROM orders o
            JOIN v_order_settlement s ON s.order_id = o.order_id
            LEFT JOIN v_refund_cap r ON r.order_id = o.order_id
            ORDER BY o.order_id
            """,
        )
        items = _rows(src, "SELECT * FROM order_items ORDER BY order_id, sku")
        shipments = _rows(src, "SELECT * FROM shipments ORDER BY order_id")
        events = _rows(src, "SELECT * FROM shipment_events ORDER BY order_id, happened_at")
        policies = _rows(src, "SELECT * FROM kb_entries ORDER BY entry_id, valid_from")
    finally:
        src.close()

    shipment_by_order = {s["order_id"]: s for s in shipments}

    with SessionLocal() as session:
        existing = session.execute(
            select(Merchant).where(Merchant.name == merchant_name)
        ).scalar_one_or_none()
        if existing and not reset:
            counts = {
                "products": len(session.execute(select(Product.id).where(Product.merchant_id == existing.id)).all()),
                "orders": len(session.execute(select(Order.id).where(Order.merchant_id == existing.id)).all()),
            }
            print(f"[seed] 已存在商家 {merchant_name!r}（id={existing.id}），跳过。加 --reset 可重建。{counts}")
            return counts
        if existing:
            reset_demo(session, existing)
            print(f"[seed] 已清理旧演示数据（merchant_id={existing.id}）")

        merchant = Merchant(name=merchant_name)
        session.add(merchant)
        session.flush()

        owner = User(
            merchant_id=merchant.id,
            email="owner@afterops.local",
            password_hash=hash_password(settings.demo_owner_password),
            role="owner",
        )
        agent = User(
            merchant_id=merchant.id,
            email="agent@afterops.local",
            password_hash=hash_password(settings.demo_agent_password),
            role="agent",
        )
        session.add_all([owner, agent])

        product_by_sku: dict[str, Product] = {}
        for row in products:
            product = Product(
                merchant_id=merchant.id,
                sku=row["sku"],
                title=row["title"],
                category=row["category"],
                description=f"{row['title']}，{row['specs']}",
                brand=row["brand"],
                specs=row["specs"],
                price_cents=row["price_cents"],
                support_7d_no_reason=bool(row["support_7d_no_reason"]),
                listed_at=_date(row["listed_at"]),
                status="active",
            )
            session.add(product)
            product_by_sku[row["sku"]] = product
        session.flush()

        for row in inventory:
            product = product_by_sku.get(row["sku"])
            if product is None:
                raise ValueError(f"库存引用了不存在的 SKU：{row['sku']}")
            session.add(
                Inventory(
                    merchant_id=merchant.id,
                    product_id=product.id,
                    warehouse_id=row["region"],
                    warehouse_name=row["warehouse"],
                    qty_available=row["qty_available"],
                    qty_reserved=0,
                )
            )

        customer_by_phone: dict[str, Customer] = {}
        for phone in sorted({o["customer_phone"] for o in orders}):
            customer = Customer(
                merchant_id=merchant.id,
                phone=phone,
                name=f"顾客{phone[-4:]}",
            )
            session.add(customer)
            customer_by_phone[phone] = customer
        session.flush()

        order_by_no: dict[str, Order] = {}
        for row in orders:
            shipment = shipment_by_order.get(row["order_id"])
            order_status, payment_status, fulfillment = _order_state(
                row["status"], row["refund_status"], shipment["status"] if shipment else None
            )
            order = Order(
                order_no=row["order_id"],
                merchant_id=merchant.id,
                customer_id=customer_by_phone[row["customer_phone"]].id,
                recipient_name=f"顾客{row['customer_phone'][-4:]}",
                recipient_phone=row["customer_phone"],
                address_region="",
                address_city=row["address_city"],
                address_detail=f"{row['address_city']}演示收货地址",
                status=order_status,
                payment_status=payment_status,
                fulfillment_status=fulfillment,
                item_total_cents=row["item_total_cents"],
                freight_cents=row["freight_cents"],
                discount_cents=row["coupon_cents"],
                payable_cents=row["payable_cents"],
                currency="CNY",
                placed_at=_dt(row["placed_at"]),
            )
            session.add(order)
            order_by_no[row["order_id"]] = order
        session.flush()

        payments: list[Payment] = []
        for row in orders:
            order = order_by_no[row["order_id"]]
            paid = order.payment_status in ("paid", "refunded", "partially_refunded")
            if order.status == "cancelled":
                payment_status = "failed"
            elif order.payment_status == "refunded":
                payment_status = "refunded"
            elif paid:
                payment_status = "paid"
            else:
                payment_status = "created"
            payments.append(
                Payment(
                    order_id=order.id,
                    merchant_id=merchant.id,
                    provider="mock",
                    provider_payment_id=f"MOCK-{row['order_id']}",
                    amount_cents=row["payable_cents"],
                    status=payment_status,
                    idempotency_key=f"seed:{row['order_id']}",
                    paid_at=_dt(row["placed_at"]) if paid else None,
                )
            )

        order_items: list[OrderItem] = []
        for row in items:
            order = order_by_no.get(row["order_id"])
            product = product_by_sku.get(row["sku"])
            if order is None or product is None:
                raise ValueError(f"订单明细引用不存在：{row['order_id']} / {row['sku']}")
            order_items.append(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    sku=row["sku"],
                    title_snapshot=product.title,
                    qty=row["qty"],
                    unit_price_cents=row["unit_price_cents"],
                )
            )

        shipment_models: list[Shipment] = []
        for row in shipments:
            order = order_by_no.get(row["order_id"])
            if order is None:
                raise ValueError(f"物流引用不存在订单：{row['order_id']}")
            shipment_models.append(
                Shipment(
                    order_id=order.id,
                    merchant_id=merchant.id,
                    carrier=row["carrier"],
                    tracking_no=row["tracking_no"],
                    status=_shipment_state(row["status"]),
                    current_node=row["current_node"],
                    shipped_at=_dt(row["shipped_at"]),
                    delivered_at=_dt(row["delivered_at"]),
                )
            )
        session.add_all(payments + order_items + shipment_models)
        session.flush()

        shipment_by_order_id = {s.order_id: s for s in shipment_models}
        shipment_events: list[ShipmentEvent] = []
        for row in events:
            order = order_by_no.get(row["order_id"])
            shipment = shipment_by_order_id.get(order.id) if order else None
            if shipment is None:
                raise ValueError(f"物流节点引用不存在运单：{row['order_id']}")
            shipment_events.append(
                ShipmentEvent(
                    shipment_id=shipment.id,
                    happened_at=_dt(row["happened_at"]),
                    node=row["node"],
                    description=row["description"],
                )
            )

        cases: list[AfterSalesCase] = []
        for row in orders:
            case_status = _refund_case_state(row["refund_status"])
            if case_status is None:
                continue
            order = order_by_no[row["order_id"]]
            cases.append(
                AfterSalesCase(
                    merchant_id=merchant.id,
                    order_id=order.id,
                    type="refund",
                    status=case_status,
                    reason=row["refund_reason"] or "退款申请",
                    amount_cents=row["refundable_max_cents"] or row["payable_cents"],
                )
            )

        policy_models: list[PolicyEntry] = []
        for row in policies:
            policy_models.append(
                PolicyEntry(
                    merchant_id=merchant.id,
                    entry_id=row["entry_id"],
                    topic=row["topic"],
                    title=row["title"],
                    body=row["body"],
                    version=row["version"],
                    valid_from=_date(row["valid_from"]),
                    valid_until=_date(row["valid_until"]),
                    published_at=_date(row["published_at"]),
                    source=row["source"],
                )
            )

        session.add_all(shipment_events + cases + policy_models)
        session.commit()

        counts = {
            "merchant_id": merchant.id,
            "products": len(products),
            "inventory": len(inventory),
            "customers": len(customer_by_phone),
            "orders": len(orders),
            "order_items": len(items),
            "payments": len(payments),
            "shipments": len(shipments),
            "shipment_events": len(events),
            "after_sales_cases": len(cases),
            "policies": len(policies),
        }
        print(f"[seed] 完成：{counts}")
        return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从合成 SQLite 店铺导入 AfterOps Postgres")
    parser.add_argument("--sqlite-path", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--merchant-name", default=settings.demo_merchant_name)
    parser.add_argument("--reset", action="store_true", help="删除并重建同名演示商家数据")
    args = parser.parse_args(argv)
    seed(args.sqlite_path, args.merchant_name, args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
