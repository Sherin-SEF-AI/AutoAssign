"""Domain errors that map to RFC 7807 problem details."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status: int = 400
    code: str = "bad_request"

    def __init__(self, detail: str, *, code: str | None = None, status: int | None = None, **extra: Any):
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.extra = extra


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"


class Unprocessable(AppError):
    status = 422
    code = "unprocessable"


class Forbidden(AppError):
    status = 403
    code = "forbidden"


class Unauthorized(AppError):
    status = 401
    code = "unauthorized"


class TooManyRequests(AppError):
    status = 429
    code = "rate_limited"


class ProviderError(Exception):
    """Raised by provider clients when a call fails after retries."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class ProviderUnavailable(ProviderError):
    """Provider skipped: circuit open, over budget or disabled."""
