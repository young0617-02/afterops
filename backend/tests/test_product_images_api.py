from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.settings import settings

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _create(client: TestClient, sku: str) -> dict:
    response = client.post(
        "/api/admin/products",
        json={
            "sku": sku,
            "title": "主图测试耳机",
            "category": "耳机",
            "brand": "声谷",
            "description": "主图测试",
            "specs": "测试",
            "price_cents": 9900,
            "support_7d_no_reason": True,
            "status": "active",
            "initial_qty": 3,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_product_image_upload_read_replace_and_delete(
    api_client: TestClient,
    shop_data,
    admin_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    sku = "SKU-IMG-001"

    assert (
        api_client.post(
            f"/api/admin/products/{sku}/image",
            files={"file": ("main.png", PNG, "image/png")},
        ).status_code
        == 401
    )

    admin_login()
    created = _create(api_client, sku)
    assert created["image_url"] is None

    uploaded = api_client.post(
        f"/api/admin/products/{sku}/image",
        files={"file": ("main.png", PNG, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    image_url = uploaded.json()["image_url"]
    assert image_url and sku in image_url and "?v=" in image_url
    store_url = image_url.replace("/api/admin/", "/api/store/")

    detail = api_client.get(f"/api/store/products/{sku}")
    assert detail.status_code == 200
    assert detail.json()["image_url"] == store_url

    image = api_client.get(store_url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG
    assert "public" in image.headers["cache-control"]
    assert len(list((tmp_path / "products").iterdir())) == 1

    replaced = api_client.post(
        f"/api/admin/products/{sku}/image",
        files={"file": ("main.jpg", JPEG, "image/jpeg")},
    )
    assert replaced.status_code == 200, replaced.text
    new_url = replaced.json()["image_url"]
    new_store_url = new_url.replace("/api/admin/", "/api/store/")
    assert new_url != image_url
    assert api_client.get(new_store_url).content == JPEG
    assert len(list((tmp_path / "products").iterdir())) == 1

    fake = api_client.post(
        f"/api/admin/products/{sku}/image",
        files={"file": ("fake.png", b"not an image", "image/png")},
    )
    assert fake.status_code == 422

    admin_login("agent@test.local", "agent123456")
    assert (
        api_client.post(
            f"/api/admin/products/{sku}/image",
            files={"file": ("main.png", PNG, "image/png")},
        ).status_code
        == 403
    )
    assert api_client.get(new_url).status_code == 200

    admin_login()
    archived = api_client.patch(f"/api/admin/products/{sku}", json={"status": "archived"})
    assert archived.status_code == 200
    assert api_client.get(new_store_url).status_code == 404

    deleted = api_client.delete(f"/api/admin/products/{sku}/image")
    assert deleted.status_code == 200
    assert deleted.json()["image_url"] is None
    assert api_client.get(new_url).status_code == 404
    assert list((tmp_path / "products").iterdir()) == []


def test_product_gallery_upload_read_limit_and_delete(
    api_client: TestClient,
    shop_data,
    admin_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    admin_login()
    sku = "SKU-GAL-001"
    _create(api_client, sku)

    for index in range(6):
        uploaded = api_client.post(
            f"/api/admin/products/{sku}/images",
            files={"file": (f"detail-{index}.png", PNG, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert len(body["gallery"]) == 6
    assert len(list((tmp_path / "products").iterdir())) == 6

    full = api_client.post(
        f"/api/admin/products/{sku}/images",
        files={"file": ("detail-7.png", PNG, "image/png")},
    )
    assert full.status_code == 409

    detail = api_client.get(f"/api/store/products/{sku}")
    assert detail.status_code == 200
    urls = detail.json()["gallery_urls"]
    assert len(urls) == 6
    image = api_client.get(urls[0])
    assert image.status_code == 200
    assert image.content == PNG
    assert "public" in image.headers["cache-control"]

    storage_name = body["gallery"][0]["storage_name"]
    deleted = api_client.delete(
        f"/api/admin/products/{sku}/images/{storage_name}"
    )
    assert deleted.status_code == 200, deleted.text
    assert len(deleted.json()["gallery"]) == 5
    assert len(list((tmp_path / "products").iterdir())) == 5
    assert api_client.get(urls[0]).status_code == 404

    archived = api_client.patch(
        f"/api/admin/products/{sku}", json={"status": "archived"}
    )
    assert archived.status_code == 200
    remaining_url = archived.json()["gallery"][0]["url"].replace(
        "/api/admin/", "/api/store/"
    )
    assert api_client.get(remaining_url).status_code == 404
