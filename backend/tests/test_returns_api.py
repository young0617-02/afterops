from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import (
    ActionProposal,
    AuditEvent,
    Inventory,
    Notification,
    Order,
    OrderItem,
    Product,
    Refund,
    ReturnRequest,
)
from app.services.audit import verify_chain
from app.settings import settings

ADDRESS = {
    "recipient_name": "退货测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "退货测试路 1 号",
}


def _paid_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code
        == 200
    )
    return body["order_no"]


def _upload_attachment(client: TestClient, name: str) -> int:
    response = client.post(
        "/api/store/attachments",
        files={
            "file": (
                name,
                bytes.fromhex("89504e470d0a1a0a") + name.encode(),
                "image/png",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _delivery(client: TestClient, order_no: str, tracking_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        response = client.post(
            f"/api/admin/orders/{order_no}/shipment/advance",
            json={"status": status},
        )
        assert response.status_code == 200, response.text


def test_return_request_flow_generates_refund_after_receipt(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    order_no = _paid_order(api_client, "return-flow-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-RETURN-0001")

    upload = api_client.post(
        "/api/store/attachments",
        files={
            "file": (
                "evidence.png",
                bytes.fromhex("89504e470d0a1a0a") + b"evidence",
                "image/png",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    attachment_id = upload.json()["id"]
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "耳机有杂音，需要退货",
            "attachment_ids": [attachment_id],
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert created.json()["status"] == "requested"
    assert created.json()["attachment_ids"] == [attachment_id]
    assert created.json()["return_address"] == ""

    duplicate = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "重复申请"},
    )
    assert duplicate.status_code == 409

    listed = api_client.get("/api/admin/returns")
    assert listed.status_code == 200, listed.text
    assert listed.json()["counts"]["requested"] == 1
    assert listed.json()["items"][0]["order_no"] == order_no

    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/returns").status_code == 200
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 403
    )

    admin_login()
    approved = api_client.post(f"/api/admin/returns/{return_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["return_address"]

    tracking = api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-RETURN-BACK-0001"},
    )
    assert tracking.status_code == 200, tracking.text
    assert tracking.json()["status"] == "in_transit"

    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"
    proposal_id = received.json()["refund_proposal_id"]
    assert proposal_id

    proposal = api_client.get("/api/admin/proposals?status=pending").json()
    assert any(item["id"] == proposal_id for item in proposal)
    approved_refund = api_client.post(
        f"/api/admin/proposals/{proposal_id}/approve"
    )
    assert approved_refund.status_code == 200, approved_refund.text
    order = api_client.get(f"/api/store/orders/{order_no}")
    assert order.json()["status"] == "refunded"

    again = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert again.status_code == 409

    db_session.expire_all()
    request = db_session.execute(select(ReturnRequest)).scalar_one()
    assert request.status == "received"
    assert request.refund_proposal_id == proposal_id
    events = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type.like("return_%"))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "return_requested",
        "return_approved",
        "return_tracking_added",
        "return_received",
    ]
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_return_rejection_and_order_state_guard(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    pending_order = _paid_order(api_client, "return-pending-0001")
    pending = api_client.post(
        "/api/store/returns",
        json={"order_no": pending_order, "reason": "未签收退货"},
    )
    assert pending.status_code == 409

    delivored_order = _paid_order(api_client, "return-reject-0001")
    admin_login()
    _delivery(api_client, delivored_order, "SF-RETURN-REJECT-0001")
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": delivored_order, "reason": "不需要了"},
    )
    assert created.status_code == 200
    return_id = created.json()["id"]
    rejected = api_client.post(
        f"/api/admin/returns/{return_id}/reject",
        json={"reason": "已超过退货期限"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejected_reason"] == "已超过退货期限"

    mine = api_client.get("/api/store/returns").json()
    assert mine[0]["status"] == "rejected"
    db_session.expire_all()
    assert db_session.execute(select(ReturnRequest)).scalar_one().status == "rejected"


def test_exchange_return_creates_replacement_order(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "exchange-return-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-EXCHANGE-0001")

    stock_before = db_session.execute(
        select(func.sum(Inventory.qty_available)).where(
            Inventory.merchant_id == shop_data["merchant"].id
        )
    ).scalar_one()
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "换一个同款",
            "resolution": "exchange",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert created.json()["resolution"] == "exchange"
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-EXCHANGE-BACK-1"},
        ).status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["status"] == "received"
    assert body["refund_proposal_id"] is None
    assert body["replacement_order_no"].startswith("ORD-")
    assert body["replacement_order_no"] != order_no

    db_session.expire_all()
    return_request = db_session.execute(select(ReturnRequest)).scalar_one()
    replacement = db_session.get(Order, return_request.replacement_order_id)
    assert replacement is not None
    assert replacement.status == "paid"
    assert replacement.payment_status == "paid"
    assert replacement.payable_cents == 0
    assert replacement.fulfillment_status == "unfulfilled"
    assert replacement.customer_id == return_request.customer_id
    assert db_session.execute(
        select(func.count(OrderItem.id)).where(OrderItem.order_id == replacement.id)
    ).scalar_one() == 1
    stock_after = db_session.execute(
        select(func.sum(Inventory.qty_available)).where(
            Inventory.merchant_id == shop_data["merchant"].id
        )
    ).scalar_one()
    # 同款换货：退回件回补 1 件，替换订单再占用 1 件，净库存与购买后一致。
    assert stock_after == stock_before
    assert body["restocked_items"] == [
        {"sku": "SKU-EAR-001", "warehouse_id": "华东", "qty": 1}
    ]
    assert db_session.execute(select(func.count(Refund.id))).scalar_one() == 0
    assert db_session.execute(select(func.count(ActionProposal.id))).scalar_one() == 0
    event_types = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.in_(
                ("exchange_order_created", "return_received")
            )
        )
    ).scalars().all()
    assert set(event_types) == {"exchange_order_created", "return_received"}


def test_exchange_replacement_shipment_visible_in_returns(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "exchange-shipment-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-EXCHANGE-SHIP-OUT")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "换货补发物流验证",
            "resolution": "exchange",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    replacement_no = received.json()["replacement_order_no"]
    assert replacement_no is not None
    assert received.json()["replacement_shipments"] == []

    shipped = api_client.post(
        f"/api/admin/orders/{replacement_no}/shipment",
        json={"carrier": "京东物流", "tracking_no": "JD-EXCHANGE-BACK-0001"},
    )
    assert shipped.status_code == 200, shipped.text
    advanced = api_client.post(
        f"/api/admin/orders/{replacement_no}/shipment/advance",
        json={"status": "in_transit"},
    )
    assert advanced.status_code == 200, advanced.text

    mine = api_client.get("/api/store/returns")
    assert mine.status_code == 200, mine.text
    row = next(item for item in mine.json() if item["id"] == return_id)
    assert row["replacement_order_no"] == replacement_no
    assert len(row["replacement_shipments"]) == 1
    shipment = row["replacement_shipments"][0]
    assert shipment["package_no"] == 1
    assert shipment["carrier"] == "京东物流"
    assert shipment["tracking_no"] == "JD-EXCHANGE-BACK-0001"
    assert shipment["status"] == "in_transit"
    assert shipment["current_node"] == "运输中"
    assert {event["node"] for event in shipment["events"]} == {
        "待揽收",
        "运输中",
    }

    admin_rows = api_client.get(
        "/api/admin/returns", params={"status": "received"}
    )
    assert admin_rows.status_code == 200, admin_rows.text
    admin_row = next(
        item for item in admin_rows.json()["items"] if item["id"] == return_id
    )
    assert admin_row["replacement_shipments"][0]["tracking_no"] == (
        "JD-EXCHANGE-BACK-0001"
    )

    db_session.expire_all()
    created_notice = db_session.execute(
        select(Notification)
        .where(
            Notification.type == "shipment_created",
            Notification.link == "/after-sales",
        )
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert created_notice is not None
    assert created_notice.title == "换货补发包裹已创建"
    assert replacement_no in created_notice.body
    updated_notice = db_session.execute(
        select(Notification)
        .where(
            Notification.type == "shipment_updated",
            Notification.link == "/after-sales",
        )
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert updated_notice is not None
    assert updated_notice.title == "换货补发运输中"
    assert "JD-EXCHANGE-BACK-0001" in updated_notice.body


def test_customer_withdraws_pending_return(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order_no = _paid_order(api_client, "return-withdraw-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-WITHDRAW-0001")

    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "误提交退货"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]

    withdrawn = api_client.post(f"/api/store/returns/{return_id}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert api_client.post(f"/api/store/returns/{return_id}/withdraw").status_code == 409
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code == 409
    )

    customer_rows = api_client.get("/api/store/returns")
    assert customer_rows.status_code == 200
    assert customer_rows.json()[0]["status"] == "withdrawn"
    admin_rows = api_client.get("/api/admin/returns")
    assert admin_rows.status_code == 200
    assert admin_rows.json()["items"] == []
    assert admin_rows.json()["counts"]["requested"] == 0

    again = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "重新提交"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] != return_id

    db_session_event_types = [
        row for row in api_client.get("/api/admin/audit").json()["items"]
        if row["event_type"].startswith("return_")
    ]
    assert any(row["event_type"] == "return_withdrawn" for row in db_session_event_types)


def test_customer_adds_return_evidence_until_limit(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    order_no = _paid_order(api_client, "return-evidence-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-EVIDENCE-0001")

    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "需要补充凭证"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]

    first = _upload_attachment(api_client, "evidence-1.png")
    second = _upload_attachment(api_client, "evidence-2.png")
    added = api_client.post(
        f"/api/store/returns/{return_id}/attachments",
        json={"attachment_ids": [first, second]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["attachment_ids"] == [first, second]

    third = _upload_attachment(api_client, "evidence-3.png")
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/attachments",
            json={"attachment_ids": [third]},
        ).status_code
        == 200
    )
    fourth = _upload_attachment(api_client, "evidence-4.png")
    overflow = api_client.post(
        f"/api/store/returns/{return_id}/attachments",
        json={"attachment_ids": [fourth]},
    )
    assert overflow.status_code == 422
    assert overflow.json()["code"] == "validation_error"

    approved = api_client.post(f"/api/admin/returns/{return_id}/approve")
    assert approved.status_code == 200, approved.text
    late = api_client.post(
        f"/api/store/returns/{return_id}/attachments",
        json={"attachment_ids": [fourth]},
    )
    assert late.status_code == 409
    assert late.json()["code"] == "conflict"


def test_cross_sku_exchange_with_difference_refund(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    charger = shop_data["charger"]
    charger_inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == charger.id)
    ).scalar_one()
    charger_inventory.qty_available = 5
    db_session.commit()

    order_no = _paid_order(api_client, "cross-sku-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-CROSS-0001")

    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "想换更便宜的充电宝",
            "resolution": "exchange",
            "exchange_sku": "SKU-PWR-001",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["exchange_sku"] == "SKU-PWR-001"
    assert body["exchange_unit_price_cents"] == 19900
    return_id = body["id"]

    assert api_client.post(f"/api/admin/returns/{return_id}/approve").status_code == 200
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-CROSS-BACK-1"},
        ).status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    received_body = received.json()
    assert received_body["status"] == "received"
    assert received_body["replacement_order_no"]
    assert received_body["refund_proposal_id"]

    replacement = api_client.get(
        f"/api/store/orders/{received_body['replacement_order_no']}"
    )
    assert replacement.status_code == 200, replacement.text
    replacement_body = replacement.json()
    assert replacement_body["payable_cents"] == 0
    assert [item["sku"] for item in replacement_body["items"]] == ["SKU-PWR-001"]

    approved = api_client.post(
        f"/api/admin/proposals/{received_body['refund_proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    original = api_client.get(f"/api/store/orders/{order_no}")
    assert original.status_code == 200
    assert original.json()["status"] == "partially_refunded"
    assert original.json()["payment"]["status"] == "partially_refunded"

    db_session.expire_all()
    refunds = db_session.execute(select(Refund)).scalars().all()
    assert len(refunds) == 1
    assert refunds[0].amount_cents == 70000
    charger_inventory = db_session.get(Inventory, charger_inventory.id)
    assert charger_inventory.qty_available == 4


def test_cross_sku_exchange_requires_supplement_payment(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    expensive = Product(
        merchant_id=shop_data["merchant"].id,
        sku="SKU-EXP-001",
        title="高价测试耳机",
        category="耳机",
        description="高价",
        brand="测试",
        specs="测试",
        price_cents=99900,
        support_7d_no_reason=True,
        status="active",
    )
    db_session.add(expensive)
    db_session.flush()
    db_session.add(
        Inventory(
            merchant_id=shop_data["merchant"].id,
            product_id=expensive.id,
            warehouse_id="华东",
            warehouse_name="上海仓",
            qty_available=5,
        )
    )
    db_session.commit()

    order_no = _paid_order(api_client, "cross-sku-expensive")
    admin_login()
    _delivery(api_client, order_no, "SF-CROSS-EXP-1")
    response = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "换高价商品",
            "resolution": "exchange",
            "exchange_sku": "SKU-EXP-001",
        },
    )
    assert response.status_code == 200, response.text
    return_id = response.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["exchange_supplement_cents"] == 10000
    assert body["exchange_payment_status"] == "pending"
    assert body["refund_proposal_id"] is None
    assert body["refund_amount_cents"] is None
    replacement_no = body["replacement_order_no"]
    assert replacement_no

    blocked = api_client.post(
        f"/api/admin/orders/{replacement_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": "SF-EXP-SUPP-0001"},
    )
    assert blocked.status_code == 409
    assert "补差价" in blocked.json()["detail"]

    mine = api_client.get("/api/store/returns").json()
    row = next(item for item in mine if item["id"] == return_id)
    assert row["exchange_supplement_cents"] == 10000
    assert row["exchange_payment_status"] == "pending"

    paid = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["exchange_payment_status"] == "paid"
    assert paid.json()["exchange_payment_ref"].startswith("EXS-")
    assert paid.json()["exchange_paid_at"] is not None
    timeline = api_client.get("/api/store/after-sales/timeline")
    assert timeline.status_code == 200, timeline.text
    assert any(
        row["title"] == "换货补差价已支付" for row in timeline.json()
    )
    again = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert again.status_code == 409

    shipped = api_client.post(
        f"/api/admin/orders/{replacement_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": "SF-EXP-SUPP-0001"},
    )
    assert shipped.status_code == 200, shipped.text

    db_session.expire_all()
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "exchange_supplement_paid"
        )
    ).scalar_one()
    assert event.payload_json["amount_cents"] == 10000
