from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "afterops-api"


def test_root_points_to_docs() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["docs"] == "/docs"


def test_readiness_ok(monkeypatch) -> None:
    monkeypatch.setattr("app.main.database_ready", lambda: (True, "ok"))
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readiness_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("app.main.database_ready", lambda: (False, "OperationalError: down"))
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"

