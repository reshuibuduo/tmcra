"""Exception hierarchy and HTTP error conversion for the TMCRA client."""

from __future__ import annotations

from typing import Any


class TMCRAError(Exception):
    """Base class for all SDK errors."""


class ConfigurationError(TMCRAError, ValueError):
    """The client configuration is invalid."""


class TransportError(TMCRAError):
    """The request could not be completed at the HTTP transport layer."""


class RequestTimeoutError(TransportError, TimeoutError):
    """The request exceeded its configured timeout."""


class ResponseValidationError(TMCRAError):
    """The server returned a successful response that does not match its schema."""

    def __init__(self, message: str, *, body: Any = None) -> None:
        super().__init__(message)
        self.body = body


class PollingTimeoutError(TMCRAError, TimeoutError):
    """Polling did not reach a terminal job state before its deadline."""

    def __init__(self, job_id: str, timeout: float) -> None:
        super().__init__(f"timed out waiting for TMCRA job {job_id} after {timeout:g}s")
        self.job_id = job_id
        self.timeout = timeout


class APIError(TMCRAError):
    """A non-success HTTP response from the service."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        detail: Any = None,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.request_id = request_id
        self.headers = headers or {}
        self.body = body


class AuthenticationError(APIError):
    """The API key is missing, invalid, or revoked."""


class AuthorizationError(APIError):
    """The API key lacks the required tenant permission."""


class NotFoundError(APIError):
    """The requested resource does not exist."""


class ConflictError(APIError):
    """The request conflicts with current server state or idempotency data."""


class ValidationError(APIError):
    """The service rejected request validation."""


class RateLimitError(APIError):
    """The service rejected the request due to pressure or rate limits."""

    @property
    def retry_after(self) -> float | None:
        value = self.headers.get("retry-after") or self.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None


class ServerError(APIError):
    """The service returned a 5xx response."""


def error_from_response(
    status_code: int,
    body: Any,
    *,
    request_id: str | None,
    headers: dict[str, str],
) -> APIError:
    code: str | None = None
    detail: Any = None
    message = f"TMCRA API request failed with HTTP {status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code")) if error.get("code") is not None else None
            detail = error.get("details", error.get("detail"))
            request_id = str(error.get("request_id") or request_id or "") or None
            if error.get("message"):
                message = str(error["message"])
        elif body.get("detail") is not None:
            detail = body["detail"]
            message = str(detail) if not isinstance(detail, dict) else str(detail)
    elif body not in (None, ""):
        message = str(body)

    error_type: type[APIError]
    if status_code == 401:
        error_type = AuthenticationError
    elif status_code == 403:
        error_type = AuthorizationError
    elif status_code == 404:
        error_type = NotFoundError
    elif status_code == 409:
        error_type = ConflictError
    elif status_code == 422:
        error_type = ValidationError
    elif status_code == 429:
        error_type = RateLimitError
    elif status_code >= 500:
        error_type = ServerError
    else:
        error_type = APIError
    return error_type(
        message,
        status_code=status_code,
        code=code,
        detail=detail,
        request_id=request_id,
        headers=headers,
        body=body,
    )
