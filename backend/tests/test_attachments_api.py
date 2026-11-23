from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_upload_and_scoped_read(api_client, shop_data, login, admin_login, otp_login, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    assert api_client.post(
        "/api/store/attachments",
        files={"file": ("evidence.png", PNG, "image/png")},
    ).status_code == 401

    login()
    uploaded = api_client.post(
        "/api/store/attachments",
        files={"file": ("evidence.png", PNG, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(PNG)
    assert len(body["sha256"]) == 64
    assert len(list(tmp_path.iterdir())) == 1

    image = api_client.get(f"/api/store/attachments/{body['id']}")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG

    with TestClient(app) as other:
        otp_login(other, "13900000021")
        assert (
            other.get(f"/api/store/attachments/{body['id']}").status_code
            == 404
        )

    admin_login()
    admin_image = api_client.get(f"/api/admin/attachments/{body['id']}")
    assert admin_image.status_code == 200
    assert admin_image.content == PNG

    admin_login("agent@test.local", "agent123456")
    assert (
        api_client.get(f"/api/admin/attachments/{body['id']}").status_code
        == 200
    )

    fake = api_client.post(
        "/api/store/attachments",
        files={"file": ("fake.png", b"not an image", "image/png")},
    )
    assert fake.status_code == 422

    oversized = api_client.post(
        "/api/store/attachments",
        files={
            "file": (
                "huge.png",
                b"\x89PNG\r\n\x1a\n" + b"0" * (settings.upload_max_bytes + 1),
                "image/png",
            )
        },
    )
    assert oversized.status_code == 422
