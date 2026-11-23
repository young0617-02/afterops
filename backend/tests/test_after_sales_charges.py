from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AfterSalesCharge, Inventory, Product

ADDRESS = {
    "recipient_name": "售后收款顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "售后收款路 1 号",
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
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
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


def _exchange_supplement(
    api_client: TestClient, shop_data, db_session, key: str
) -> tuple[str, int, str]:
    target = Product(
        merchant_id=shop_data["merchant"].id,
        sku="SKU-CHARGE-EXP-001",
        title="收款台账高价耳机",
        category="耳机",
        description="高价",
        brand="测试",
        specs="测试",
        price_cents=99900,
        support_7d_no_reason=True,
        status="active",
    )
    db_session.add(target)
    db_session.flush()
    db_session.add(
        Inventory(
            merchant_id=shop_data["merchant"].id,
            product_id=target.id,
            warehouse_id="华东",
            warehouse_name="上海仓",
            qty_available=5,
        )
    )
    db_session.commit()

    order_no = _paid_order(api_client, key)
    _delivery(api_client, order_no, f"SF-{key}")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "补差收款测试",
            "resolution": "exchange",
            "exchange_sku": target.sku,
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
    assert received.json()["exchange_payment_status"] == "pending"
    paid = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert paid.status_code == 200, paid.text
    return order_no, return_id, paid.json()["exchange_payment_ref"]


def _repair_fee(
    api_client: TestClient, key: str
) -> tuple[str, int, str]:
    order_no = _paid_order(api_client, key)
    _delivery(api_client, order_no, f"SF-{key}")
    created = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "右耳无声",
        },
    )
    assert created.status_code == 200, created.text
    repair_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/approve").status_code
        == 200
    )
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/receive").status_code
        == 200
    )
    quoted = api_client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": 5000, "note": "更换主板"},
    )
    assert quoted.status_code == 200, quoted.text
    accepted = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/accept"
    )
    assert accepted.status_code == 200, accepted.text
    paid = api_client.post(f"/api/store/repairs/{repair_id}/quote/pay")
    assert paid.status_code == 200, paid.text
    return order_no, repair_id, paid.json()["quote_payment_ref"]


def test_after_sales_charges_ledger(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    exchange_order, return_id, exchange_ref = _exchange_supplement(
        api_client, shop_data, db_session, "charges-exchange-0001"
    )
    repair_order, repair_id, repair_ref = _repair_fee(
        api_client, "charges-repair-0001"
    )

    ledger = api_client.get(
        "/api/admin/finance/after-sales-charges?days=30"
    )
    assert ledger.status_code == 200, ledger.text
    body = ledger.json()
    assert body["count"] == 2
    assert body["exchange_supplement_cents"] == 10000
    assert body["repair_fee_cents"] == 5000
    assert body["total_cents"] == 15000
    assert body["total_display"] == "150.00"
    kinds = {item["kind"]: item for item in body["items"]}
    assert set(kinds) == {"exchange_supplement", "repair_fee"}
    exchange_charge = kinds["exchange_supplement"]
    assert exchange_charge["order_no"] == exchange_order
    assert exchange_charge["reference"] == exchange_ref
    assert exchange_charge["return_request_id"] == return_id
    assert exchange_charge["repair_request_id"] is None
    assert exchange_charge["amount_cents"] == 10000
    repair_charge = kinds["repair_fee"]
    assert repair_charge["order_no"] == repair_order
    assert repair_charge["reference"] == repair_ref
    assert repair_charge["repair_request_id"] == repair_id
    assert repair_charge["return_request_id"] is None
    assert repair_charge["amount_cents"] == 5000

    duplicate_exchange = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert duplicate_exchange.status_code == 409
    duplicate_repair = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/pay"
    )
    assert duplicate_repair.status_code == 409
    unchanged = api_client.get(
        "/api/admin/finance/after-sales-charges?days=30"
    ).json()
    assert unchanged["count"] == 2

    db_session.expire_all()
    charges = db_session.execute(select(AfterSalesCharge)).scalars().all()
    assert len(charges) == 2
    old = datetime.now(UTC) - timedelta(days=40)
    for charge in charges:
        charge.created_at = old
    db_session.commit()

    outside_window = api_client.get(
        "/api/admin/finance/after-sales-charges?days=30"
    ).json()
    assert outside_window["count"] == 0
    assert outside_window["total_cents"] == 0
    assert outside_window["items"] == []
    inside_window = api_client.get(
        "/api/admin/finance/after-sales-charges?days=90"
    ).json()
    assert inside_window["count"] == 2
    assert inside_window["total_cents"] == 15000
