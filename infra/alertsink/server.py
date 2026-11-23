from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALERTS: list[dict] = []
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "afterops-alert-sink/0.1"

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            return self._json(200, {"status": "ok"})
        if self.path.rstrip("/") == "/alerts":
            with LOCK:
                return self._json(200, {"count": len(ALERTS), "alerts": ALERTS[-20:]})
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/alerts":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        with LOCK:
            ALERTS.append(payload)
            del ALERTS[:-50]
        alerts = payload.get("alerts") or []
        print(
            json.dumps(
                {
                    "event": "alerts_received",
                    "status": payload.get("status"),
                    "count": len(alerts),
                    "alertnames": [
                        alert.get("labels", {}).get("alertname") for alert in alerts
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return self._json(200, {"received": len(alerts)})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[alert-sink] {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    print("[alert-sink] listening on 0.0.0.0:8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
