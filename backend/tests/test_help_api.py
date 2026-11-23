from __future__ import annotations

from fastapi.testclient import TestClient


def test_help_center_lists_current_policies(api_client: TestClient, shop_data) -> None:
    response = api_client.get("/api/store/help")
    assert response.status_code == 200, response.text
    body = response.json()
    topics = {row["topic"]: row for row in body["policies"]}
    assert set(topics) == {"refund_time", "return"}
    assert topics["return"]["version"] == "v2"
    assert topics["return"]["entry_id"] == "POLICY-RETURN"
    assert "人工核定" in topics["return"]["body"]
    assert topics["refund_time"]["body"]
    assert body["return_policy"] == {
        "window_days": 7,
        "return_shipping_fee_cents": 0,
        "allow_exchange": True,
        "final_sale_categories": [],
        "category_windows": [],
    }


def test_help_center_reflects_policy_changes(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    updated = api_client.patch(
        "/api/admin/return-policy",
        json={
            "window_days": 15,
            "return_shipping_fee_cents": 800,
            "allow_exchange": False,
            "final_sale_categories": ["耳机"],
        },
    )
    assert updated.status_code == 200, updated.text

    published = api_client.post(
        "/api/admin/policies",
        json={
            "entry_id": "POLICY-RETURN",
            "topic": "return",
            "title": "15 天退货（新版）",
            "body": "签收后 15 天内可申请退货，耳机类不支持无理由退货。",
            "source": "商家后台",
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] == "v3"

    body = api_client.get("/api/store/help").json()
    topics = {row["topic"]: row for row in body["policies"]}
    assert topics["return"]["version"] == "v3"
    assert "15 天" in topics["return"]["body"]
    assert body["return_policy"] == {
        "window_days": 15,
        "return_shipping_fee_cents": 800,
        "allow_exchange": False,
        "final_sale_categories": ["耳机"],
        "category_windows": [],
    }
    return_rows = [
        row for row in body["policies"] if row["topic"] == "return"
    ]
    assert len(return_rows) == 1
    assert return_rows[0]["version"] == "v3"
