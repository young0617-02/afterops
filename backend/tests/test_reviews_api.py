from __future__ import annotations

import base64
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent

ADDRESS = {
    "recipient_name": "评价测试顾客",
    "recipient_phone": "13800000008",
    "region": "华东",
    "city": "上海",
    "detail": "评价测试路 8 号",
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


def _delivery(client: TestClient, order_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-REV-{uuid4().hex[:8]}"},
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


def _create_review(
    client: TestClient, order_no: str, rating: int = 5, content: str = "很好用"
):
    return client.post(
        "/api/store/reviews",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "rating": rating,
            "content": content,
        },
    )


def test_create_review_and_public_summary(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "review-create")
    admin_login()
    _delivery(api_client, order_no)

    created = _create_review(api_client, order_no)
    assert created.status_code == 200, created.text
    assert created.json()["rating"] == 5
    assert created.json()["status"] == "visible"

    mine = api_client.get("/api/store/reviews/mine")
    assert mine.status_code == 200, mine.text
    assert any(
        row["order_no"] == order_no and row["sku"] == "SKU-EAR-001"
        for row in mine.json()
    )

    public = api_client.get("/api/store/products/SKU-EAR-001/reviews")
    assert public.status_code == 200, public.text
    body = public.json()
    assert body["count"] == 1
    assert body["average"] == 5.0
    assert body["reviews"][0]["content"] == "很好用"
    assert "customer_phone" not in body["reviews"][0]
    assert body["reviews"][0]["customer_name"]

    duplicate = _create_review(api_client, order_no)
    assert duplicate.status_code == 409
    assert "已评价" in duplicate.json()["detail"]


def test_review_guards_ownership_and_validation(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "review-guards")
    admin_login()

    not_delivered = _create_review(api_client, order_no)
    assert not_delivered.status_code == 409
    assert "已签收" in not_delivered.json()["detail"]

    _delivery(api_client, order_no)
    wrong_sku = api_client.post(
        "/api/store/reviews",
        json={
            "order_no": order_no,
            "sku": "SKU-PWR-001",
            "rating": 5,
        },
    )
    assert wrong_sku.status_code == 422
    assert "不属于该订单" in wrong_sku.json()["detail"]

    invalid = _create_review(api_client, order_no, rating=6)
    assert invalid.status_code == 422

    api_client.delete("/api/store/session")
    login("13800000010")
    other = _create_review(api_client, order_no)
    assert other.status_code == 404


def test_admin_hides_and_restores_review(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "review-hide")
    admin_login()
    _delivery(api_client, order_no)
    created = _create_review(api_client, order_no, content="需要隐藏")
    assert created.status_code == 200, created.text
    review_id = created.json()["id"]

    admin_rows = api_client.get("/api/admin/reviews")
    assert admin_rows.status_code == 200, admin_rows.text
    assert any(row["id"] == review_id for row in admin_rows.json())

    hidden = api_client.patch(
        f"/api/admin/reviews/{review_id}", json={"status": "hidden"}
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["status"] == "hidden"
    public = api_client.get("/api/store/products/SKU-EAR-001/reviews").json()
    assert public["count"] == 0
    assert public["reviews"] == []

    restored = api_client.patch(
        f"/api/admin/reviews/{review_id}", json={"status": "visible"}
    )
    assert restored.status_code == 200, restored.text
    public = api_client.get("/api/store/products/SKU-EAR-001/reviews").json()
    assert public["count"] == 1

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "review_visibility_changed")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["review_id"] == review_id

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert (
        api_client.patch(
            f"/api/admin/reviews/{review_id}", json={"status": "hidden"}
        ).status_code
        == 403
    )


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_review_with_images_and_visibility(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "review-images")
    admin_login()
    _delivery(api_client, order_no)

    uploaded = api_client.post(
        "/api/store/attachments",
        files={"file": ("review.png", PNG, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment_id = uploaded.json()["id"]
    created = api_client.post(
        "/api/store/reviews",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "rating": 4,
            "content": "有图评价",
            "attachment_ids": [attachment_id],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["image_urls"] == [
        f"/api/store/attachments/{attachment_id}"
    ]

    public = api_client.get("/api/store/products/SKU-EAR-001/reviews").json()
    review = next(row for row in public["reviews"] if row["content"] == "有图评价")
    assert review["image_urls"] == [
        f"/api/store/reviews/{review['id']}/images/{attachment_id}"
    ]
    image = api_client.get(review["image_urls"][0])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "review_created")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["attachment_count"] == 1

    hidden = api_client.patch(
        f"/api/admin/reviews/{review['id']}",
        json={"status": "hidden"},
    )
    assert hidden.status_code == 200, hidden.text
    assert api_client.get(review["image_urls"][0]).status_code == 404
    assert (
        api_client.get(f"/api/admin/attachments/{attachment_id}").status_code
        == 200
    )


def test_owner_replies_to_review(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "review-reply")
    admin_login()
    _delivery(api_client, order_no)
    created = _create_review(api_client, order_no, content="希望改进包装")
    assert created.status_code == 200, created.text
    review_id = created.json()["id"]

    reply = api_client.patch(
        f"/api/admin/reviews/{review_id}/reply",
        json={"reply": "感谢反馈，我们会改进包装。"},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["merchant_reply"] == "感谢反馈，我们会改进包装。"
    assert reply.json()["replied_at"] is not None

    public = api_client.get("/api/store/products/SKU-EAR-001/reviews").json()
    row = next(item for item in public["reviews"] if item["id"] == review_id)
    assert row["merchant_reply"] == "感谢反馈，我们会改进包装。"
    assert row["replied_at"] is not None

    updated = api_client.patch(
        f"/api/admin/reviews/{review_id}/reply",
        json={"reply": "已反馈给仓储团队。"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["merchant_reply"] == "已反馈给仓储团队。"

    cleared = api_client.patch(
        f"/api/admin/reviews/{review_id}/reply",
        json={"reply": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["merchant_reply"] == ""
    assert cleared.json()["replied_at"] is None

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.patch(
        f"/api/admin/reviews/{review_id}/reply",
        json={"reply": "客服不应能回复"},
    )
    assert forbidden.status_code == 403

    events = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type == "review_replied"
        )
    ).scalars().all()
    assert events == ["review_replied", "review_replied", "review_replied"]


def test_review_follow_up_once_with_images(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "review-follow-up")
    admin_login()
    _delivery(api_client, order_no)
    review = _create_review(api_client, order_no, content="初评很好").json()
    uploaded = api_client.post(
        "/api/store/attachments",
        files={"file": ("follow-up.png", PNG, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment_id = uploaded.json()["id"]

    followed = api_client.post(
        f"/api/store/reviews/{review['id']}/follow-up",
        json={"content": "用了一周仍然不错", "attachment_ids": [attachment_id]},
    )
    assert followed.status_code == 200, followed.text
    body = followed.json()
    assert body["follow_up_content"] == "用了一周仍然不错"
    assert body["follow_up_image_urls"] == [
        f"/api/store/attachments/{attachment_id}"
    ]
    assert body["followed_up_at"]

    public = api_client.get(
        "/api/store/products/SKU-EAR-001/reviews"
    ).json()
    row = next(item for item in public["reviews"] if item["id"] == review["id"])
    assert row["follow_up_content"] == "用了一周仍然不错"
    assert row["follow_up_image_urls"] == [
        f"/api/store/reviews/{review['id']}/images/{attachment_id}"
    ]
    assert api_client.get(row["follow_up_image_urls"][0]).status_code == 200

    duplicate = api_client.post(
        f"/api/store/reviews/{review['id']}/follow-up",
        json={"content": "再次追评"},
    )
    assert duplicate.status_code == 409
    assert "已追评" in duplicate.json()["detail"]
    empty = api_client.post(
        f"/api/store/reviews/{review['id']}/follow-up",
        json={"content": "   "},
    )
    assert empty.status_code == 422

    admin_login()
    listed = api_client.get("/api/admin/reviews").json()
    admin_row = next(item for item in listed if item["id"] == review["id"])
    assert admin_row["follow_up_content"] == "用了一周仍然不错"
    assert admin_row["follow_up_image_urls"] == [
        f"/api/admin/attachments/{attachment_id}"
    ]
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "review_followed_up")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["review_id"] == review["id"]
    assert audit.payload_json["attachment_count"] == 1


def test_review_follow_up_rejects_hidden_expired_and_foreign(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.domain.models import ProductReview

    login()
    order_no = _paid_order(api_client, "review-follow-up-guard")
    admin_login()
    _delivery(api_client, order_no)
    review = _create_review(api_client, order_no, content="待隐藏").json()
    hidden = api_client.patch(
        f"/api/admin/reviews/{review['id']}", json={"status": "hidden"}
    )
    assert hidden.status_code == 200
    blocked = api_client.post(
        f"/api/store/reviews/{review['id']}/follow-up",
        json={"content": "隐藏后追评"},
    )
    assert blocked.status_code == 409
    assert "已隐藏" in blocked.json()["detail"]

    order_no2 = _paid_order(api_client, "review-follow-up-expired")
    _delivery(api_client, order_no2)
    old_review = _create_review(
        api_client, order_no2, content="过期追评"
    ).json()
    db_session.expire_all()
    stored = db_session.get(ProductReview, old_review["id"])
    assert stored is not None
    stored.created_at = datetime.now(UTC) - timedelta(days=31)
    db_session.commit()
    expired = api_client.post(
        f"/api/store/reviews/{old_review['id']}/follow-up",
        json={"content": "超过 30 天"},
    )
    assert expired.status_code == 409
    assert "30 天" in expired.json()["detail"]

    order_no3 = _paid_order(api_client, "review-follow-up-foreign")
    _delivery(api_client, order_no3)
    own_review = _create_review(
        api_client, order_no3, content="归属校验"
    ).json()
    other = TestClient(api_client.app)
    with other:
        otp = other.post(
            "/api/store/otp/request", json={"phone": "13900000045"}
        )
        code = otp.json()["debug_code"]
        assert (
            other.post(
                "/api/store/session",
                json={"phone": "13900000045", "code": code},
            ).status_code
            == 200
        )
        foreign = other.post(
            f"/api/store/reviews/{own_review['id']}/follow-up",
            json={"content": "越权追评"},
        )
        assert foreign.status_code == 404
