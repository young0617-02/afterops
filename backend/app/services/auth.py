from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

COOKIE_NAME = "afterops_session"
ADMIN_COOKIE_NAME = "afterops_admin"
SESSION_TTL_SECONDS = 7 * 24 * 3600


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload: dict[str, Any], secret: str) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


def _verify(token: str, secret: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expired = int(payload.get("exp", 0)) < int(time.time())
    except (TypeError, ValueError):
        return None
    return None if expired else payload


def sign_session_token(
    customer_id: int,
    merchant_id: int,
    secret: str,
    ttl: int = SESSION_TTL_SECONDS,
) -> str:
    return _sign(
        {"cid": customer_id, "mid": merchant_id, "exp": int(time.time()) + ttl},
        secret,
    )


def verify_session_token(token: str, secret: str) -> dict[str, Any] | None:
    return _verify(token, secret)


def sign_admin_token(
    user_id: int,
    merchant_id: int,
    role: str,
    secret: str,
    ttl: int = SESSION_TTL_SECONDS,
) -> str:
    return _sign(
        {
            "uid": user_id,
            "mid": merchant_id,
            "role": role,
            "exp": int(time.time()) + ttl,
        },
        secret,
    )


def verify_admin_token(token: str, secret: str) -> dict[str, Any] | None:
    return _verify(token, secret)
