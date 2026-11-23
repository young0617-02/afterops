from __future__ import annotations

import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.csrf import SESSION_CSRF_PAIRS, csrf_token_for, set_csrf_cookie
from app.settings import settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT_PREFIXES = ("/api/webhooks/",)
CSRF_EXEMPT_POST_PATHS = {
    "/api/store/otp/request",
    "/api/store/session",
    "/api/admin/session",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'none'",
}


def _csrf_required(request: Request) -> bool:
    if request.method not in UNSAFE_METHODS:
        return False
    path = request.url.path
    if path.startswith(CSRF_EXEMPT_PREFIXES):
        return False
    return not (
        request.method == "POST" and path in CSRF_EXEMPT_POST_PATHS
    )


def _csrf_failed(request: Request) -> bool:
    for session_cookie, _, csrf_header in SESSION_CSRF_PAIRS:
        session_token = request.cookies.get(session_cookie)
        if not session_token:
            continue
        supplied = request.headers.get(csrf_header, "")
        if not supplied or not hmac.compare_digest(
            supplied, csrf_token_for(session_token)
        ):
            return True
    return False


def _bootstrap_csrf_cookies(request: Request, response) -> None:
    if request.method in UNSAFE_METHODS or not request.url.path.startswith("/api/"):
        return
    for session_cookie, csrf_cookie, _ in SESSION_CSRF_PAIRS:
        session_token = request.cookies.get(session_cookie)
        if not session_token:
            continue
        if request.cookies.get(csrf_cookie) != csrf_token_for(session_token):
            set_csrf_cookie(response, csrf_cookie, session_token)


def install_security(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method in UNSAFE_METHODS
            and origin is not None
            and origin.rstrip("/") not in settings.allowed_origin_set
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "请求来源不被允许",
                    "code": "origin_not_allowed",
                },
                headers=SECURITY_HEADERS,
            )
        if _csrf_required(request) and _csrf_failed(request):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "CSRF 校验失败，请刷新页面后重试",
                    "code": "csrf_token_invalid",
                },
                headers=SECURITY_HEADERS,
            )
        response = await call_next(request)
        _bootstrap_csrf_cookies(request, response)
        response.headers.update(SECURITY_HEADERS)
        if settings.app_env == "prod":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response
