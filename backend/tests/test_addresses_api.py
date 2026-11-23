from __future__ import annotations

from fastapi.testclient import TestClient


def _address(**overrides) -> dict:
    payload = {
        "label": "家",
        "recipient_name": "地址顾客",
        "recipient_phone": "13800000001",
        "region": "华东",
        "city": "上海",
        "detail": "地址簿路 1 号",
        "is_default": False,
    }
    payload.update(overrides)
    return payload


def _checkout(client: TestClient, key: str, detail: str) -> dict:
    added = client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    assert added.status_code == 200, added.text
    response = client.post(
        "/api/store/checkout",
        headers={"Idempotency-Key": key},
        json={
            "address": {
                "recipient_name": "订单地址",
                "recipient_phone": "13800000001",
                "region": "华东",
                "city": "上海",
                "detail": detail,
            }
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_address_crud_and_default(api_client: TestClient, shop_data, login) -> None:
    login()
    assert api_client.get("/api/store/addresses").json() == []

    first = api_client.post("/api/store/addresses", json=_address(detail="一号地址"))
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["is_default"] is True
    assert first_body["label"] == "家"

    second = api_client.post(
        "/api/store/addresses",
        json=_address(detail="二号地址", label="公司"),
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["is_default"] is False

    third = api_client.post(
        "/api/store/addresses",
        json=_address(detail="三号地址", is_default=True),
    )
    assert third.status_code == 200, third.text
    rows = api_client.get("/api/store/addresses").json()
    assert [row["id"] for row in rows] == [
        third.json()["id"],
        second_body["id"],
        first_body["id"],
    ]
    assert [row["is_default"] for row in rows] == [True, False, False]

    updated = api_client.patch(
        f"/api/store/addresses/{second_body['id']}",
        json=_address(detail="二号地址改", label="公司改", is_default=True),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["detail"] == "二号地址改"
    assert updated.json()["is_default"] is True
    default_rows = {
        row["id"]: row["is_default"]
        for row in api_client.get("/api/store/addresses").json()
    }
    assert default_rows == {
        first_body["id"]: False,
        second_body["id"]: True,
        third.json()["id"]: False,
    }

    deleted = api_client.delete(f"/api/store/addresses/{second_body['id']}")
    assert deleted.status_code == 200
    remaining = api_client.get("/api/store/addresses").json()
    assert {row["id"] for row in remaining} == {
        first_body["id"],
        third.json()["id"],
    }
    assert sum(row["is_default"] for row in remaining) == 1


def test_address_ownership_and_validation(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    created = api_client.post("/api/store/addresses", json=_address()).json()

    invalid_phone = api_client.post(
        "/api/store/addresses", json=_address(recipient_phone="12345678901")
    )
    assert invalid_phone.status_code == 422
    assert invalid_phone.json()["code"] == "validation_error"

    blank_name = api_client.post(
        "/api/store/addresses", json=_address(recipient_name="   ")
    )
    assert blank_name.status_code == 422

    otp_login(api_client, "13800000002")
    assert api_client.get("/api/store/addresses").json() == []
    assert (
        api_client.patch(
            f"/api/store/addresses/{created['id']}", json=_address()
        ).status_code
        == 404
    )
    assert (
        api_client.delete(f"/api/store/addresses/{created['id']}").status_code == 404
    )


def test_address_limit_and_duplicate(
    api_client: TestClient, shop_data, login
) -> None:
    login()
    for index in range(10):
        created = api_client.post(
            "/api/store/addresses",
            json=_address(detail=f"限量地址 {index}"),
        )
        assert created.status_code == 200, created.text

    duplicate = api_client.post(
        "/api/store/addresses", json=_address(detail="限量地址 0")
    )
    assert duplicate.status_code == 200
    assert len(api_client.get("/api/store/addresses").json()) == 10

    overflow = api_client.post(
        "/api/store/addresses", json=_address(detail="第十一个地址")
    )
    assert overflow.status_code == 409
    assert overflow.json()["code"] == "conflict"


def test_last_address_prefers_default(
    api_client: TestClient, shop_data, login
) -> None:
    login()
    _checkout(api_client, "address-last-order", "订单地址路 9 号")
    last = api_client.get("/api/store/addresses/last")
    assert last.status_code == 200
    assert last.json()["detail"] == "订单地址路 9 号"

    saved = api_client.post(
        "/api/store/addresses",
        json=_address(detail="默认地址路 8 号", is_default=True),
    )
    assert saved.status_code == 200
    last = api_client.get("/api/store/addresses/last")
    assert last.status_code == 200
    assert last.json()["detail"] == "默认地址路 8 号"
