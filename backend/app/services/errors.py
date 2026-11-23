from __future__ import annotations


class ServiceError(Exception):
    status_code = 400
    code = "service_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(ServiceError):
    status_code = 422
    code = "validation_error"


class UnauthorizedError(ServiceError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(ServiceError):
    status_code = 403
    code = "forbidden"


class NotFoundError(ServiceError):
    status_code = 404
    code = "not_found"


class ConflictError(ServiceError):
    status_code = 409
    code = "conflict"


class RateLimitError(ServiceError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ServiceUnavailableError(ServiceError):
    status_code = 503
    code = "unavailable"
