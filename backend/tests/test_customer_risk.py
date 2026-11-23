from __future__ import annotations

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "风险画像顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "风险画像路 1 号",
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


def _return(client: TestClient, order_no: str) -> int:
    created = client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "风险画像测试"},
    )
    assert created.status_code == 200, created.text
    return int(created.json()["id"])


def _detail(client: TestClient, phone: str) -> dict:
    rows = client.get(
        "/api/admin/customers", params={"q": phone}
    ).json()
    assert rows, "顾客目录未找到顾客"
    customer_id = rows[0]["id"]
    detail = client.get(f"/api/admin/customers/{customer_id}")
    assert detail.status_code == 200, detail.text
    return detail.json()


def test_customer_risk_escalates_from_medium_to_high(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login("13800000011")
    admin_login()
    first_order = _paid_order(api_client, "risk-orders-0001")
    _delivery(api_client, first_order, "SF-RISK-0001")
    _return(api_client, first_order)

    first = _detail(api_client, "13800000011")["after_sales_risk"]
    assert first["level"] == "low"
    assert first["return_count"] == 1
    assert first["order_count"] == 1
    assert first["return_rate"] == 1.0
    assert first["window_days"] == 90
    assert any("退货 1 次" in reason for reason in first["reasons"])

    second_order = _paid_order(api_client, "risk-orders-0002")
    _delivery(api_client, second_order, "SF-RISK-0002")
    _return(api_client, second_order)
    second = _detail(api_client, "13800000011")["after_sales_risk"]
    assert second["level"] == "medium"
    assert second["return_count"] == 2

    third_order = _paid_order(api_client, "risk-orders-0003")
    _delivery(api_client, third_order, "SF-RISK-0003")
    third_return_id = _return(api_client, third_order)

    high = _detail(api_client, "13800000011")["after_sales_risk"]
    assert high["level"] == "high"
    assert high["return_count"] == 3
    assert high["order_count"] == 3
    assert high["return_rate"] == 1.0
    assert high["cancelled_exchange_count"] == 0
    assert high["appeal_count"] == 0
    assert high["customer_responsibility_count"] == 0
    assert high["last_return_at"] is not None
    assert any("退货 3 次" in reason for reason in high["reasons"])

    admin_returns = api_client.get(
        "/api/admin/returns", params={"status": "all"}
    )
    assert admin_returns.status_code == 200, admin_returns.text
    admin_row = next(
        item
        for item in admin_returns.json()["items"]
        if item["id"] == third_return_id
    )
    assert admin_row["customer_risk_level"] == "high"
    assert any(
        "退货 3 次" in reason
        for reason in admin_row["customer_risk_reasons"]
    )

    customer_returns = api_client.get("/api/store/returns")
    assert customer_returns.status_code == 200, customer_returns.text
    assert customer_returns.json()
    assert all(
        item["customer_risk_level"] is None
        for item in customer_returns.json()
    )
    assert all(
        item["customer_risk_reasons"] == []
        for item in customer_returns.json()
    )


def test_customer_risk_low_without_orders(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login("13800000012")
    admin_login()
    risk = _detail(api_client, "13800000012")["after_sales_risk"]
    assert risk["level"] == "low"
    assert risk["order_count"] == 0
    assert risk["return_count"] == 0
    assert risk["return_rate"] == 0.0
    assert risk["reasons"] == []
    assert risk["last_return_at"] is None
