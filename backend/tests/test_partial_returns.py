from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    ActionProposal,
    AuditEvent,
    Inventory,
    InventoryMovement,
    Order,
    OrderItem,
    Product,
    ReturnRequest,
)

ADDRESS = {
    "recipient_name": "部分退货顾客",
    "recipient_phone": "13800000004",
    "region": "华东",
    "city": "上海",
    "detail": "部分退货路 4 号",
}


def _stock(db_session, product_id: int, warehouse_id: str = "华东") -> int:
    db_session.expire_all()
    return db_session.execute(
        select(Inventory).where(
            Inventory.product_id == product_id,
            Inventory.warehouse_id == warehouse_id,
        )
    ).scalar_one().qty_available


def _enable_charger(db_session, quantity: int = 5) -> None:
    charger = db_session.execute(
        select(Product).where(Product.sku == "SKU-PWR-001")
    ).scalar_one()
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == charger.id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    inventory.qty_available = quantity
    db_session.commit()


def _paid_multi_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    client.post("/api/store/cart/items", json={"sku": "SKU-PWR-001", "qty": 1})
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


def _paid_earphone_pair(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 2})
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


def _delivery(client: TestClient, order_no: str, tracking_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert (
            client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )


def _return_and_receive(
    client: TestClient, order_no: str, tracking_no: str, items=None
) -> tuple[dict, dict]:
    payload = {
        "order_no": order_no,
        "reason": "部分退货测试",
    }
    if items is not None:
        payload["items"] = items
    created = client.post("/api/store/returns", json=payload)
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        client.post(f"/api/admin/returns/{return_id}/approve").status_code == 200
    )
    assert (
        client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": tracking_no},
        ).status_code
        == 200
    )
    received = client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    return created.json(), received.json()


def test_partial_return_refunds_selected_item_and_restocks_it(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    earphone_id = shop_data["earphone"].id
    charger_id = shop_data["charger"].id
    order_no = _paid_multi_order(api_client, "partial-return-1")
    _delivery(api_client, order_no, "SF-PARTIAL-001")
    assert _stock(db_session, earphone_id) == 9
    assert _stock(db_session, charger_id) == 4

    created, received = _return_and_receive(
        api_client,
        order_no,
        "SF-PARTIAL-RETURN-1",
        items=[{"sku": "SKU-EAR-001", "qty": 1}],
    )
    assert [
        (line["sku"], line["title"], line["qty"], line["disposition"])
        for line in created["items"]
    ] == [("SKU-EAR-001", "降噪耳机 Pro", 1, None)]
    assert received["restocked_items"] == [
        {"sku": "SKU-EAR-001", "warehouse_id": "华东", "qty": 1}
    ]
    assert _stock(db_session, earphone_id) == 10
    assert _stock(db_session, charger_id) == 4

    proposal = db_session.get(ActionProposal, received["refund_proposal_id"])
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 89900

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["refund_scope"] == "partial"
    assert audit.payload_json["refund_amount_cents"] == 89900


def test_full_return_without_items_refunds_remaining_and_restocks_all(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    earphone_id = shop_data["earphone"].id
    charger_id = shop_data["charger"].id
    order_no = _paid_multi_order(api_client, "partial-return-full")
    _delivery(api_client, order_no, "SF-PARTIAL-002")

    created, received = _return_and_receive(
        api_client, order_no, "SF-PARTIAL-RETURN-2"
    )
    assert {item["sku"] for item in created["items"]} == {
        "SKU-EAR-001",
        "SKU-PWR-001",
    }
    assert _stock(db_session, earphone_id) == 10
    assert _stock(db_session, charger_id) == 5

    proposal = db_session.get(ActionProposal, received["refund_proposal_id"])
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 109800
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["refund_scope"] == "full"


def test_partial_quantity_return_refunds_one_unit(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    earphone_id = shop_data["earphone"].id
    order_no = _paid_earphone_pair(api_client, "partial-qty")
    _delivery(api_client, order_no, "SF-PARTIAL-003")
    assert _stock(db_session, earphone_id) == 8

    created, received = _return_and_receive(
        api_client,
        order_no,
        "SF-PARTIAL-RETURN-3",
        items=[{"sku": "SKU-EAR-001", "qty": 1}],
    )
    assert [
        (line["sku"], line["title"], line["qty"], line["disposition"])
        for line in created["items"]
    ] == [("SKU-EAR-001", "降噪耳机 Pro", 1, None)]
    assert received["restocked_items"][0]["qty"] == 1
    assert _stock(db_session, earphone_id) == 9
    proposal = db_session.get(ActionProposal, received["refund_proposal_id"])
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 89900


def test_partial_selection_validation(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    order_no = _paid_multi_order(api_client, "partial-invalid")
    _delivery(api_client, order_no, "SF-PARTIAL-004")

    def create(items, resolution="refund"):
        return api_client.post(
            "/api/store/returns",
            json={
                "order_no": order_no,
                "reason": "非法选择",
                "items": items,
                "resolution": resolution,
            },
        )

    unknown = create([{"sku": "SKU-NOPE-001", "qty": 1}])
    assert unknown.status_code == 422
    assert "不属于该订单" in unknown.json()["detail"]

    duplicate = create(
        [{"sku": "SKU-EAR-001", "qty": 1}, {"sku": "SKU-EAR-001", "qty": 1}]
    )
    assert duplicate.status_code == 422
    assert "重复" in duplicate.json()["detail"]

    over = create([{"sku": "SKU-EAR-001", "qty": 2}])
    assert over.status_code == 422
    assert "超出订单数量" in over.json()["detail"]

    assert db_session.execute(select(ReturnRequest)).scalars().all() == []


def test_return_inspection_restocks_good_and_writes_off_damaged(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    shop_data["earphone"].cost_cents = 30000
    shop_data["charger"].cost_cents = 10000
    db_session.commit()
    earphone_id = shop_data["earphone"].id
    charger_id = shop_data["charger"].id
    order_no = _paid_multi_order(api_client, "return-inspection")
    _delivery(api_client, order_no, "SF-INSPECT-001")

    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "外观检查",
            "items": [
                {"sku": "SKU-EAR-001", "qty": 1},
                {"sku": "SKU-PWR-001", "qty": 1},
            ],
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    item_ids = {
        line["sku"]: line["id"] for line in created.json()["items"]
    }
    assert api_client.post(
        f"/api/admin/returns/{return_id}/approve"
    ).status_code == 200
    assert api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-INSPECT-BACK"},
    ).status_code == 200

    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_ids["SKU-EAR-001"],
                    "disposition": "restock",
                },
                {
                    "item_id": item_ids["SKU-PWR-001"],
                    "disposition": "damaged",
                },
            ]
        },
    )
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["restocked_items"] == [
        {"sku": "SKU-EAR-001", "warehouse_id": "华东", "qty": 1}
    ]
    assert body["damaged_items"] == [
        {"sku": "SKU-PWR-001", "title": "快充移动电源", "qty": 1}
    ]
    dispositions = {
        line["sku"]: line["disposition"] for line in body["items"]
    }
    assert dispositions == {
        "SKU-EAR-001": "restock",
        "SKU-PWR-001": "damaged",
    }
    assert _stock(db_session, earphone_id) == 10
    assert _stock(db_session, charger_id) == 4

    movements = db_session.execute(
        select(InventoryMovement).where(
            InventoryMovement.return_id == return_id
        )
    ).scalars().all()
    assert len(movements) == 1
    assert movements[0].product_id == earphone_id
    assert movements[0].qty_delta == 1
    assert movements[0].reason == "return_restocked"

    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert {
        item.sku: item.disposition for item in request.items
    } == {"SKU-EAR-001": "restock", "SKU-PWR-001": "damaged"}
    assert all(item.inspected_by is not None for item in request.items)
    assert all(item.inspected_at is not None for item in request.items)
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["damaged_items"] == [
        {"sku": "SKU-PWR-001", "qty": 1}
    ]

    customer_returns = api_client.get("/api/store/returns").json()
    row = next(item for item in customer_returns if item["id"] == return_id)
    assert {
        line["sku"]: line["disposition"] for line in row["items"]
    } == {"SKU-EAR-001": "restock", "SKU-PWR-001": "damaged"}

    analytics = api_client.get(
        "/api/admin/returns/analytics?days=30"
    ).json()
    assert analytics["restocked_units"] == 1
    assert analytics["damaged_units"] == 1
    assert analytics["damaged_cost_cents"] == 10000


def test_return_inspection_validation_and_all_damaged(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    charger_id = shop_data["charger"].id
    order_no = _paid_multi_order(api_client, "return-inspection-bad")
    _delivery(api_client, order_no, "SF-INSPECT-002")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "质检校验",
            "items": [
                {"sku": "SKU-EAR-001", "qty": 1},
                {"sku": "SKU-PWR-001", "qty": 1},
            ],
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    item_ids = [
        line["id"] for line in created.json()["items"]
    ]
    assert api_client.post(
        f"/api/admin/returns/{return_id}/approve"
    ).status_code == 200
    assert api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-INSPECT-BACK-2"},
    ).status_code == 200

    unknown = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"items": [{"item_id": 999999, "disposition": "restock"}]},
    )
    assert unknown.status_code == 422
    assert "退货明细不存在" in unknown.json()["detail"]

    duplicate = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {"item_id": item_ids[0], "disposition": "restock"},
                {"item_id": item_ids[0], "disposition": "damaged"},
            ]
        },
    )
    assert duplicate.status_code == 422

    invalid = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"items": [{"item_id": item_ids[0], "disposition": "lost"}]},
    )
    assert invalid.status_code == 422

    all_damaged = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {"item_id": item_ids[0], "disposition": "damaged"},
                {"item_id": item_ids[1], "disposition": "damaged"},
            ]
        },
    )
    assert all_damaged.status_code == 200, all_damaged.text
    body = all_damaged.json()
    assert body["restocked_items"] == []
    assert body["damaged_items"] == [
        {"sku": "SKU-EAR-001", "title": "降噪耳机 Pro", "qty": 1},
        {"sku": "SKU-PWR-001", "title": "快充移动电源", "qty": 1},
    ]
    assert _stock(db_session, charger_id) == 4
    assert db_session.execute(
        select(InventoryMovement).where(
            InventoryMovement.return_id == return_id
        )
    ).scalars().all() == []


def test_partial_multi_item_exchange_with_per_item_sku(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    earphone_id = shop_data["earphone"].id
    charger_id = shop_data["charger"].id
    order_no = _paid_multi_order(api_client, "partial-exchange-1")
    _delivery(api_client, order_no, "SF-PARTIAL-EX-1")

    def create(items, resolution="exchange", exchange_sku=None):
        payload = {
            "order_no": order_no,
            "reason": "换货测试",
            "items": items,
            "resolution": resolution,
        }
        if exchange_sku:
            payload["exchange_sku"] = exchange_sku
        return api_client.post("/api/store/returns", json=payload)

    higher = create(
        [{"sku": "SKU-PWR-001", "qty": 1, "exchange_sku": "SKU-EAR-001"}]
    )
    assert higher.status_code == 200, higher.text
    assert higher.json()["items"][0]["exchange_unit_price_cents"] == 89900
    assert (
        api_client.post(
            f"/api/store/returns/{higher.json()['id']}/withdraw"
        ).status_code
        == 200
    )

    global_multi = create(
        [
            {"sku": "SKU-EAR-001", "qty": 1},
            {"sku": "SKU-PWR-001", "qty": 1},
        ],
        exchange_sku="SKU-PWR-001",
    )
    assert global_multi.status_code == 409
    assert "按明细" in global_multi.json()["detail"]

    refund_with_target = create(
        [{"sku": "SKU-EAR-001", "qty": 1, "exchange_sku": "SKU-PWR-001"}],
        resolution="refund",
    )
    assert refund_with_target.status_code == 422

    created = create(
        [{"sku": "SKU-EAR-001", "qty": 1, "exchange_sku": "SKU-PWR-001"}]
    )
    assert created.status_code == 200, created.text
    body = created.json()
    return_id = body["id"]
    assert body["exchange_sku"] == "SKU-PWR-001"
    assert body["items"][0]["exchange_sku"] == "SKU-PWR-001"
    assert body["items"][0]["exchange_unit_price_cents"] == 19900

    assert api_client.post(
        f"/api/admin/returns/{return_id}/approve"
    ).status_code == 200
    assert api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-PARTIAL-EX-BACK-1"},
    ).status_code == 200
    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive"
    )
    assert received.status_code == 200, received.text
    received_body = received.json()
    assert received_body["replacement_order_no"]
    assert received_body["refund_proposal_id"]
    assert received_body["refund_amount_cents"] == 70000
    assert received_body["items"][0]["exchange_sku"] == "SKU-PWR-001"

    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    replacement = db_session.get(Order, request.replacement_order_id)
    assert replacement is not None
    replacement_items = db_session.execute(
        select(OrderItem).where(OrderItem.order_id == replacement.id)
    ).scalars().all()
    assert len(replacement_items) == 1
    assert replacement_items[0].sku == "SKU-PWR-001"
    assert replacement_items[0].qty == 1
    proposal = db_session.get(ActionProposal, received_body["refund_proposal_id"])
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 70000
    assert _stock(db_session, earphone_id) == 10
    assert _stock(db_session, charger_id) == 3

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["difference_cents"] == 70000
    assert audit.payload_json["exchange_items"] == [
        {"sku": "SKU-EAR-001", "exchange_sku": "SKU-PWR-001", "qty": 1}
    ]


def test_multi_item_exchange_mixed_targets(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    order_no = _paid_multi_order(api_client, "partial-exchange-2")
    _delivery(api_client, order_no, "SF-PARTIAL-EX-2")

    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "两件都换",
            "resolution": "exchange",
            "items": [
                {
                    "sku": "SKU-EAR-001",
                    "qty": 1,
                    "exchange_sku": "SKU-PWR-001",
                },
                {"sku": "SKU-PWR-001", "qty": 1},
            ],
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    targets = {
        line["sku"]: line["exchange_sku"]
        for line in created.json()["items"]
    }
    assert targets == {"SKU-EAR-001": "SKU-PWR-001", "SKU-PWR-001": None}

    assert api_client.post(
        f"/api/admin/returns/{return_id}/approve"
    ).status_code == 200
    assert api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-PARTIAL-EX-BACK-2"},
    ).status_code == 200
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["refund_amount_cents"] == 70000
    assert body["restocked_items"][0]["sku"] == "SKU-EAR-001" or any(
        line["sku"] == "SKU-EAR-001" for line in body["restocked_items"]
    )

    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    replacement = db_session.get(Order, request.replacement_order_id)
    assert replacement is not None
    replacement_items = db_session.execute(
        select(OrderItem)
        .where(OrderItem.order_id == replacement.id)
        .order_by(OrderItem.id)
    ).scalars().all()
    assert [(item.sku, item.qty) for item in replacement_items] == [
        ("SKU-PWR-001", 2)
    ]
    proposal = db_session.get(ActionProposal, body["refund_proposal_id"])
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 70000
