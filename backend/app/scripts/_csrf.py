from __future__ import annotations

import httpx

from app.services.csrf import (
    ADMIN_CSRF_COOKIE,
    ADMIN_CSRF_HEADER,
    CUSTOMER_CSRF_COOKIE,
    CUSTOMER_CSRF_HEADER,
)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CsrfClient(httpx.Client):
    def request(self, method, url, **kwargs):
        if method.upper() in UNSAFE_METHODS:
            headers = httpx.Headers(kwargs.pop("headers", None))
            if not headers.get(CUSTOMER_CSRF_HEADER):
                token = self.cookies.get(CUSTOMER_CSRF_COOKIE)
                if token:
                    headers[CUSTOMER_CSRF_HEADER] = token
            if not headers.get(ADMIN_CSRF_HEADER):
                token = self.cookies.get(ADMIN_CSRF_COOKIE)
                if token:
                    headers[ADMIN_CSRF_HEADER] = token
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)
