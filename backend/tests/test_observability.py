from __future__ import annotations

from fastapi.testclient import TestClient


def test_request_id_is_echoed(api_client: TestClient) -> None:
    supplied = api_client.get("/health", headers={"X-Request-Id": "test-request-1"})
    assert supplied.status_code == 200
    assert supplied.headers["x-request-id"] == "test-request-1"

    generated = api_client.get("/health")
    assert generated.headers.get("x-request-id")


def test_metrics_endpoint(api_client: TestClient) -> None:
    api_client.get("/health")
    response = api_client.get("/metrics")
    assert response.status_code == 200
    assert "afterops_http_requests_total" in response.text
    assert 'path="/health"' in response.text
    assert "afterops_http_request_duration_seconds_bucket" in response.text
    assert "afterops_http_request_duration_seconds_count" in response.text
