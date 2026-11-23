from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Inventory, Order, Shipment
from app.services.orders import auto_complete_delivered_orders

ADDRESS = {
    "recipient_name": "拆包测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "拆包测试路 1 号",
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
) -> str:
    for sku, qty in items:
        added = client.post(
            "/api/store/cart/items", json={"sku": sku, "qty": qty}
        )
        assert added.status_code == 200, added.text
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
    return body["order_no"]


def _order_items(client: TestClient, order_no: str) -> dict[str, int]:
    rows = client.get("/api/admin/orders", params={"open_only": "false"}).json()
    row = next(item for item in rows if item["order_no"] == order_no)
    return {item["sku"]: item["order_item_id"] for item in row["items"]}


def _create_package(
    client: TestClient,
    order_no: str,
    tracking_no: str,
    items: list[dict[str, int]] | None = None,
):
    payload: dict[str, object] = {
        "carrier": "顺丰速运",
        "tracking_no": tracking_no,
    }
    if items is not None:
        payload["items"] = items
    return client.post(
        f"/api/admin/orders/{order_no}/shipment", json=payload
    )


def _advance(
    client: TestClient,
    order_no: str,
    status: str,
    shipment_id: int | None = None,
):
    payload: dict[str, object] = {"status": status}
    if shipment_id is not None:
        payload["shipment_id"] = shipment_id
    return client.post(
        f"/api/admin/orders/{order_no}/shipment/advance", json=payload
    )


def _deliver_package(
    client: TestClient, order_no: str, shipment_id: int
) -> None:
    for status in ("in_transit", "out_for_delivery", "delivered"):
        response = _advance(client, order_no, status, shipment_id)
        assert response.status_code == 200, response.text


def test_split_package_lifecycle_completes_order_only_after_all_delivered(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order_no = _paid_order(
        api_client,
        "split-package-0001",
        (("SKU-EAR-001", 2), ("SKU-PWR-001", 1)),
    )
    admin_login()
    item_ids = _order_items(api_client, order_no)

    listed = api_client.get("/api/admin/orders").json()
    row = next(item for item in listed if item["order_no"] == order_no)
    assert row["shipments"] == []
    remaining = {item["sku"]: item["remaining_qty"] for item in row["items"]}
    assert remaining == {"SKU-EAR-001": 2, "SKU-PWR-001": 1}

    first = _create_package(
        api_client,
        order_no,
        "SF-PKG-0001",
        [{"order_item_id": item_ids["SKU-EAR-001"], "qty": 1}],
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["package_no"] == 1
    assert first_body["item_count"] == 1
    assert first_body["order_status"] == "fulfilling"
    assert first_body["fulfillment_status"] == "allocated"

    detail = api_client.get(f"/api/admin/orders/{order_no}").json()
    assert len(detail["shipments"]) == 1
    assert detail["shipments"][0]["package_no"] == 1
    assert [
        (item["sku"], item["qty"]) for item in detail["shipments"][0]["items"]
    ] == [("SKU-EAR-001", 1)]

    listed = api_client.get("/api/admin/orders").json()
    row = next(item for item in listed if item["order_no"] == order_no)
    shipped = {item["sku"]: item["shipped_qty"] for item in row["items"]}
    remaining = {item["sku"]: item["remaining_qty"] for item in row["items"]}
    assert shipped == {"SKU-EAR-001": 1, "SKU-PWR-001": 0}
    assert remaining == {"SKU-EAR-001": 1, "SKU-PWR-001": 1}

    for status in ("in_transit", "out_for_delivery"):
        assert _advance(api_client, order_no, status).status_code == 200
    delivered_first = _advance(api_client, order_no, "delivered")
    assert delivered_first.status_code == 200, delivered_first.text
    assert delivered_first.json()["order_status"] == "shipped"
    assert delivered_first.json()["fulfillment_status"] == "shipped"

    customer = api_client.get(f"/api/store/orders/{order_no}").json()
    assert customer["status"] == "shipped"
    assert customer["fulfillment_status"] == "shipped"
    assert len(customer["shipments"]) == 1
    assert customer["shipments"][0]["status"] == "delivered"

    second = _create_package(
        api_client,
        order_no,
        "SF-PKG-0002",
        [
            {"order_item_id": item_ids["SKU-EAR-001"], "qty": 1},
            {"order_item_id": item_ids["SKU-PWR-001"], "qty": 1},
        ],
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["package_no"] == 2
    assert second_body["item_count"] == 2
    assert second_body["order_status"] == "shipped"

    for status in ("in_transit", "out_for_delivery"):
        assert (
            _advance(
                api_client, order_no, status, second_body["shipment_id"]
            ).status_code
            == 200
        )
    completed = _advance(
        api_client, order_no, "delivered", second_body["shipment_id"]
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["order_status"] == "delivered"
    assert completed.json()["fulfillment_status"] == "delivered"

    customer = api_client.get(f"/api/store/orders/{order_no}").json()
    assert customer["status"] == "delivered"
    assert len(customer["shipments"]) == 2
    assert [
        shipment["package_no"] for shipment in customer["shipments"]
    ] == [1, 2]
    assert [
        (item["sku"], item["qty"])
        for item in customer["shipments"][1]["items"]
    ] == [("SKU-EAR-001", 1), ("SKU-PWR-001", 1)]
    assert customer["shipments"][1]["status"] == "delivered"

    confirmed = api_client.post(f"/api/store/orders/{order_no}/confirm-receipt")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"


def test_over_shipment_unknown_items_and_package_selector(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order_no = _paid_order(
        api_client,
        "split-package-validation-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    admin_login()
    item_ids = _order_items(api_client, order_no)

    over = _create_package(
        api_client,
        order_no,
        "SF-VALID-0001",
        [{"order_item_id": item_ids["SKU-EAR-001"], "qty": 2}],
    )
    assert over.status_code == 409
    assert "最多还能发 1 件" in over.json()["detail"]

    unknown = _create_package(
        api_client,
        order_no,
        "SF-VALID-0002",
        [{"order_item_id": 999999, "qty": 1}],
    )
    assert unknown.status_code == 422

    zero = _create_package(
        api_client,
        order_no,
        "SF-VALID-0003",
        [{"order_item_id": item_ids["SKU-EAR-001"], "qty": 0}],
    )
    assert zero.status_code == 422

    first = _create_package(
        api_client,
        order_no,
        "SF-VALID-0004",
        [{"order_item_id": item_ids["SKU-EAR-001"], "qty": 1}],
    )
    assert first.status_code == 200
    first_id = first.json()["shipment_id"]

    second = _create_package(
        api_client,
        order_no,
        "SF-VALID-0005",
    )
    assert second.status_code == 200, second.text
    assert second.json()["package_no"] == 2
    assert second.json()["item_count"] == 1
    second_id = second.json()["shipment_id"]

    duplicate_tracking = _create_package(
        api_client,
        order_no,
        "SF-VALID-0004",
    )
    assert duplicate_tracking.status_code == 409

    ambiguous = _advance(api_client, order_no, "in_transit")
    assert ambiguous.status_code == 422
    assert "指定要更新的包裹" in ambiguous.json()["detail"]

    wrong_package = _advance(api_client, order_no, "in_transit", 999999)
    assert wrong_package.status_code == 404

    assert (
        _advance(api_client, order_no, "in_transit", first_id).status_code
        == 200
    )
    assert (
        _advance(api_client, order_no, "in_transit", second_id).status_code
        == 200
    )
    exhausted = _create_package(api_client, order_no, "SF-VALID-0006")
    assert exhausted.status_code == 409
    assert "已全部发货" in exhausted.json()["detail"]


def test_auto_complete_waits_for_last_package_delivery(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order_no = _paid_order(
        api_client,
        "split-package-autocomplete-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    admin_login()
    item_ids = _order_items(api_client, order_no)

    first = _create_package(
        api_client,
        order_no,
        "SF-AUTO-PKG-0001",
        [{"order_item_id": item_ids["SKU-EAR-001"], "qty": 1}],
    ).json()
    second = _create_package(
        api_client,
        order_no,
        "SF-AUTO-PKG-0002",
        [{"order_item_id": item_ids["SKU-PWR-001"], "qty": 1}],
    ).json()
    _deliver_package(api_client, order_no, first["shipment_id"])
    _deliver_package(api_client, order_no, second["shipment_id"])

    order = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    shipments = db_session.execute(
        select(Shipment)
        .where(Shipment.order_id == order.id)
        .order_by(Shipment.package_no)
    ).scalars().all()
    assert len(shipments) == 2
    shipments[0].delivered_at = datetime.now(UTC) - timedelta(days=8)
    shipments[1].delivered_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()

    assert auto_complete_delivered_orders(db_session, days=7) == 0
    db_session.expire_all()
    assert (
        db_session.execute(
            select(Order.status).where(Order.id == order.id)
        ).scalar_one()
        == "delivered"
    )

    shipments = db_session.execute(
        select(Shipment)
        .where(Shipment.order_id == order.id)
        .order_by(Shipment.package_no)
    ).scalars().all()
    shipments[1].delivered_at = datetime.now(UTC) - timedelta(days=8)
    db_session.commit()
    assert auto_complete_delivered_orders(db_session, days=7) == 1
    db_session.expire_all()
    assert (
        db_session.execute(
            select(Order.status).where(Order.id == order.id)
        ).scalar_one()
        == "completed"
    )
