from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import (
    AuditEvent,
    Inventory,
    InventoryReservation,
    Order,
    OrderItem,
    ProductReview,
    Refund,
)
from app.services.audit import verify_chain

ADDRESS = {
    "recipient_name": "部分取消顾客",
    "recipient_phone": "13800000011",
    "region": "华东",
    "city": "上海",
    "detail": "部分取消路 11 号",
}


def _stock_charger(db_session, shop_data) -> None:
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == shop_data["charger"].id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    inventory.qty_available = 5
    db_session.commit()


def _paid_order(
    client: TestClient,
    key: str,
    items: tuple[tuple[str, int], ...] = (("SKU-EAR-001", 1),),
) -> dict:
    for sku, qty in items:
        response = client.post(
            "/api/store/cart/items", json={"sku": sku, "qty": qty}
        )
        assert response.status_code == 200, response.text
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    return body


def _cancel(
    client: TestClient,
    order_no: str,
    items: list[tuple[str, int]] | None = None,
):
    payload = (
        {"items": [{"sku": sku, "qty": qty} for sku, qty in items]}
        if items is not None
        else None
    )
    return client.post(
        f"/api/store/orders/{order_no}/cancel-request",
        json=payload if payload is not None else {},
    )


def _order_detail(client: TestClient, order_no: str) -> dict:
    response = client.get(f"/api/store/orders/{order_no}")
    assert response.status_code == 200, response.text
    return response.json()


def _item_by_sku(detail: dict, sku: str) -> dict:
    return next(item for item in detail["items"] if item["sku"] == sku)


def _deliver(client: TestClient, order_no: str, shipment_id: int) -> None:
    for status in ("in_transit", "out_for_delivery", "delivered"):
        response = client.post(
            f"/api/admin/orders/{order_no}/shipment/advance",
            json={"status": status, "shipment_id": shipment_id},
        )
        assert response.status_code == 200, response.text


def test_partial_cancel_refunds_restocks_and_ships_remaining_item(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order = _paid_order(
        api_client,
        "partial-cancel-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    order_no = order["order_no"]
    detail = _order_detail(api_client, order_no)
    assert _item_by_sku(detail, "SKU-EAR-001")["cancelable_qty"] == 1
    assert _item_by_sku(detail, "SKU-PWR-001")["cancelable_qty"] == 1

    requested = _cancel(api_client, order_no, [("SKU-PWR-001", 1)])
    assert requested.status_code == 200, requested.text
    body = requested.json()
    assert body["scope"] == "partial"
    assert body["amount_cents"] == 19900
    assert body["amount_display"] == "199.00"
    assert body["items"] == [{"sku": "SKU-PWR-001", "qty": 1}]
    assert _order_detail(api_client, order_no)["cancel_requested_at"] is not None

    admin_login()
    approved = api_client.post(
        f"/api/admin/proposals/{body['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    assert order_row.status == "paid"
    assert order_row.payment_status == "partially_refunded"
    assert order_row.cancel_requested_at is None
    charger_item = db_session.execute(
        select(OrderItem).where(
            OrderItem.order_id == order_row.id,
            OrderItem.sku == "SKU-PWR-001",
        )
    ).scalar_one()
    earphone_item = db_session.execute(
        select(OrderItem).where(
            OrderItem.order_id == order_row.id,
            OrderItem.sku == "SKU-EAR-001",
        )
    ).scalar_one()
    assert charger_item.cancelled_qty == 1
    assert earphone_item.cancelled_qty == 0

    refund = db_session.execute(
        select(Refund).where(Refund.order_id == order_row.id)
    ).scalar_one()
    assert refund.kind == "refund"
    assert refund.amount_cents == 19900
    charger_inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == shop_data["charger"].id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    assert charger_inventory.qty_available == 5
    charger_reservation = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == order_row.id,
            InventoryReservation.product_id == shop_data["charger"].id,
        )
    ).scalar_one_or_none()
    assert charger_reservation is None

    detail = _order_detail(api_client, order_no)
    assert _item_by_sku(detail, "SKU-PWR-001")["cancelled_qty"] == 1
    assert _item_by_sku(detail, "SKU-PWR-001")["cancelable_qty"] == 0
    assert _item_by_sku(detail, "SKU-EAR-001")["cancelable_qty"] == 1

    listed = api_client.get("/api/admin/orders").json()
    row = next(item for item in listed if item["order_no"] == order_no)
    fulfillment_items = {item["sku"]: item for item in row["items"]}
    assert fulfillment_items["SKU-PWR-001"]["cancelled_qty"] == 1
    assert fulfillment_items["SKU-PWR-001"]["remaining_qty"] == 0
    assert fulfillment_items["SKU-EAR-001"]["remaining_qty"] == 1

    created = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={
            "carrier": "顺丰速运",
            "tracking_no": f"SF-PC-{uuid4().hex[:8]}",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["item_count"] == 1
    shipment_id = created.json()["shipment_id"]
    admin_detail = api_client.get(f"/api/admin/orders/{order_no}").json()
    assert [
        (item["sku"], item["qty"])
        for item in admin_detail["shipments"][0]["items"]
    ] == [("SKU-EAR-001", 1)]

    _deliver(api_client, order_no, shipment_id)
    delivered = _order_detail(api_client, order_no)
    assert delivered["status"] == "delivered"
    assert delivered["payment_status"] == "partially_refunded"
    confirmed = api_client.post(f"/api/store/orders/{order_no}/confirm-receipt")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "order_partially_cancelled")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["items"] == [
        {
            "order_item_id": charger_item.id,
            "sku": "SKU-PWR-001",
            "qty": 1,
        }
    ]
    assert audit.payload_json["all_cancelled"] is False
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_partial_cancel_applies_discount_share_then_full_remaining(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    admin_login()
    coupon = api_client.post(
        "/api/admin/coupons",
        json={
            "code": "CANCEL100",
            "discount_type": "fixed",
            "value": 10000,
            "min_spend_cents": 0,
            "starts_on": "2026-01-01",
            "ends_on": "2026-12-31",
            "max_uses": 10,
            "per_customer_limit": 1,
        },
    )
    assert coupon.status_code == 200, coupon.text

    login()
    for sku, qty in (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)):
        assert (
            api_client.post(
                "/api/store/cart/items", json={"sku": sku, "qty": qty}
            ).status_code
            == 200
        )
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS, "coupon_code": "CANCEL100"},
        headers={"Idempotency-Key": "partial-cancel-discount-0001"},
    )
    assert checkout.status_code == 201, checkout.text
    order = checkout.json()
    assert order["discount_cents"] == 10000
    assert order["amount_cents"] == 99800
    assert (
        api_client.post(
            f"/api/store/payments/{order['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    order_no = order["order_no"]

    first = _cancel(api_client, order_no, [("SKU-PWR-001", 1)])
    assert first.status_code == 200, first.text
    assert first.json()["scope"] == "partial"
    assert first.json()["amount_cents"] == 18088

    approved = api_client.post(
        f"/api/admin/proposals/{first.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    detail = _order_detail(api_client, order_no)
    assert detail["payment_status"] == "partially_refunded"
    assert _item_by_sku(detail, "SKU-PWR-001")["cancelled_qty"] == 1
    assert _item_by_sku(detail, "SKU-EAR-001")["cancelable_qty"] == 1

    second = _cancel(api_client, order_no, [("SKU-EAR-001", 1)])
    assert second.status_code == 200, second.text
    assert second.json()["scope"] == "full"
    assert second.json()["amount_cents"] == 81712
    approved = api_client.post(
        f"/api/admin/proposals/{second.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    assert order_row.status == "refunded"
    assert order_row.payment_status == "refunded"
    refunds = db_session.execute(
        select(Refund)
        .where(Refund.order_id == order_row.id)
        .order_by(Refund.id)
    ).scalars().all()
    assert [refund.amount_cents for refund in refunds] == [18088, 81712]
    assert sum(refund.amount_cents for refund in refunds) == 99800


def test_assigned_quantity_is_not_cancelable_and_pending_cancel_blocks_shipping(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order = _paid_order(
        api_client,
        "partial-cancel-assigned-0001",
        (("SKU-EAR-001", 2), ("SKU-PWR-001", 1)),
    )
    order_no = order["order_no"]

    admin_login()
    listed = api_client.get("/api/admin/orders").json()
    row = next(item for item in listed if item["order_no"] == order_no)
    item_ids = {item["sku"]: item["order_item_id"] for item in row["items"]}
    first = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={
            "carrier": "顺丰速运",
            "tracking_no": f"SF-PC-{uuid4().hex[:8]}",
            "items": [
                {"order_item_id": item_ids["SKU-EAR-001"], "qty": 1}
            ],
        },
    )
    assert first.status_code == 200, first.text
    shipment_id = first.json()["shipment_id"]

    detail = _order_detail(api_client, order_no)
    earphone = _item_by_sku(detail, "SKU-EAR-001")
    assert earphone["shipped_qty"] == 1
    assert earphone["cancelable_qty"] == 1
    assert _item_by_sku(detail, "SKU-PWR-001")["cancelable_qty"] == 1

    over = _cancel(api_client, order_no, [("SKU-EAR-001", 2)])
    assert over.status_code == 409
    assert "最多还能取消 1 件" in over.json()["detail"]

    requested = _cancel(
        api_client,
        order_no,
        [("SKU-EAR-001", 1), ("SKU-PWR-001", 1)],
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["scope"] == "partial"

    blocked = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={
            "carrier": "顺丰速运",
            "tracking_no": f"SF-PC-{uuid4().hex[:8]}",
        },
    )
    assert blocked.status_code == 409
    assert "取消申请" in blocked.json()["detail"]

    approved = api_client.post(
        f"/api/admin/proposals/{requested.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    detail = _order_detail(api_client, order_no)
    earphone = _item_by_sku(detail, "SKU-EAR-001")
    assert earphone["cancelled_qty"] == 1
    assert earphone["shipped_qty"] == 1
    assert earphone["cancelable_qty"] == 0
    assert _item_by_sku(detail, "SKU-PWR-001")["cancelled_qty"] == 1

    exhausted = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={
            "carrier": "顺丰速运",
            "tracking_no": f"SF-PC-{uuid4().hex[:8]}",
        },
    )
    assert exhausted.status_code == 409
    assert "已全部发货" in exhausted.json()["detail"]

    _deliver(api_client, order_no, shipment_id)
    delivered = _order_detail(api_client, order_no)
    assert delivered["status"] == "delivered"
    assert delivered["payment_status"] == "partially_refunded"

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    assert order_row.cancel_requested_at is None


def test_cancel_all_unshipped_items_is_full_refund(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order = _paid_order(
        api_client,
        "partial-cancel-all-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    order_no = order["order_no"]
    requested = _cancel(
        api_client,
        order_no,
        [("SKU-EAR-001", 1), ("SKU-PWR-001", 1)],
    )
    assert requested.status_code == 200, requested.text
    body = requested.json()
    assert body["scope"] == "full"
    assert body["amount_cents"] == order["amount_cents"]

    admin_login()
    approved = api_client.post(
        f"/api/admin/proposals/{body['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    assert order_row.status == "refunded"
    assert order_row.payment_status == "refunded"
    items = db_session.execute(
        select(OrderItem).where(OrderItem.order_id == order_row.id)
    ).scalars().all()
    assert all(item.cancelled_qty == item.qty for item in items)
    reservations = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == order_row.id
        )
    ).scalars().all()
    assert reservations == []
    detail = _order_detail(api_client, order_no)
    assert all(item["cancelable_qty"] == 0 for item in detail["items"])

    inventory_total = db_session.execute(
        select(func.sum(Inventory.qty_available)).where(
            Inventory.product_id.in_(
                [shop_data["earphone"].id, shop_data["charger"].id]
            )
        )
    ).scalar_one()
    assert inventory_total == 20


def test_cancelled_items_are_blocked_from_returns_repairs_and_reviews(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order = _paid_order(
        api_client,
        "partial-cancel-aftersales-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    order_no = order["order_no"]
    requested = _cancel(api_client, order_no, [("SKU-PWR-001", 1)])
    assert requested.status_code == 200, requested.text

    admin_login()
    approved = api_client.post(
        f"/api/admin/proposals/{requested.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    created = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={
            "carrier": "顺丰速运",
            "tracking_no": f"SF-PC-{uuid4().hex[:8]}",
        },
    )
    assert created.status_code == 200, created.text
    _deliver(api_client, order_no, created.json()["shipment_id"])
    assert _order_detail(api_client, order_no)["status"] == "delivered"

    returned = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "测试已取消商品退货",
            "items": [{"sku": "SKU-PWR-001", "qty": 1}],
        },
    )
    assert returned.status_code == 422
    assert "已取消" in returned.json()["detail"]

    repair_eligibility = api_client.get(
        f"/api/store/orders/{order_no}/repair-eligibility"
    )
    assert repair_eligibility.status_code == 200, repair_eligibility.text
    assert [
        item["sku"] for item in repair_eligibility.json()["items"]
    ] == ["SKU-EAR-001"]
    repair = api_client.post(
        "/api/store/repairs",
        json={"order_no": order_no, "sku": "SKU-PWR-001", "issue": "已取消商品"},
    )
    assert repair.status_code == 409
    assert "已取消" in repair.json()["detail"]

    review = api_client.post(
        "/api/store/reviews",
        json={"order_no": order_no, "sku": "SKU-PWR-001", "rating": 5},
    )
    assert review.status_code == 409
    assert "已取消" in review.json()["detail"]
    earphone_review = api_client.post(
        "/api/store/reviews",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "rating": 5,
            "content": "收到商品，正常",
        },
    )
    assert earphone_review.status_code == 200, earphone_review.text
    assert (
        db_session.execute(select(func.count(ProductReview.id))).scalar_one()
        == 1
    )
