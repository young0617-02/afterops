from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import Response

from app.services.auth import ADMIN_COOKIE_NAME, COOKIE_NAME, SESSION_TTL_SECONDS
from app.settings import settings

CUSTOMER_CSRF_COOKIE = "afterops_csrf"
ADMIN_CSRF_COOKIE = "afterops_admin_csrf"
CUSTOMER_CSRF_HEADER = "X-CSRF-Token"
ADMIN_CSRF_HEADER = "X-Admin-CSRF-Token"

SESSION_CSRF_PAIRS = (
    (COOKIE_NAME, CUSTOMER_CSRF_COOKIE, CUSTOMER_CSRF_HEADER),
    (ADMIN_COOKIE_NAME, ADMIN_CSRF_COOKIE, ADMIN_CSRF_HEADER),
)


def csrf_token_for(session_token: str) -> str:
    digest = hmac.new(
        settings.session_secret.encode("utf-8"),
        f"csrf:{session_token}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def set_csrf_cookie(response: Response, cookie_name: str, session_token: str) -> None:
    response.set_cookie(
        cookie_name,
        csrf_token_for(session_token),
        max_age=SESSION_TTL_SECONDS,
        httponly=False,
        samesite="lax",
        secure=settings.app_env == "prod",
        path="/",
    )
