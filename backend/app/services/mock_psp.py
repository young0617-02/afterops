from __future__ import annotations

import hashlib
import hmac


def mock_signature(event_id: str, payment_id: int, status: str, secret: str) -> str:
    payload = f"{event_id}:{payment_id}:{status}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
