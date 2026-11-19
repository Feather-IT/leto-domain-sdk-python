"""Exceptions of the SDK and the mapping from error responses to them (see the API's ERROR_CODES)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ._response import ResponseInfo
from ._retry_after import parse_retry_after
from .types import DomainQuoteDto

__all__ = [
    "ApiConnectionError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "IdempotencyInProgressError",
    "IdempotencyKeyReusedError",
    "InsufficientBalanceError",
    "InvalidArgumentError",
    "IpNotAllowedError",
    "LetoApiError",
    "LetoError",
    "NotFoundError",
    "PermissionDeniedError",
    "PriceChangedError",
    "QuoteDeclinedError",
    "RateLimitError",
    "ServerError",
    "UnexpectedResponseError",
    "ValidationError",
]


class LetoError(Exception):
    """Base class of every exception this SDK raises. Catch it to handle all SDK errors at once."""


class InvalidArgumentError(LetoError, ValueError):
    """A method or option got a value the SDK cannot send (for example an empty domain name).

    Nothing was sent to the API. It is also a ValueError.
    """


class LetoApiError(LetoError):
    """The API answered with an error status (4xx or 5xx).

    Branch on ``error_code`` (the ``code`` of the error body, for example ``DOMAIN_NOT_FOUND``),
    not on the message: codes are stable and only added, messages can change.

    Subclasses group the common cases (NotFoundError, PriceChangedError, ...), so you can catch
    those instead of comparing codes. Every error response is still a LetoApiError.
    """

    http_status: int
    """HTTP status of the response."""
    error_code: str
    """Stable error code, for example ``DOMAIN_NOT_FOUND``. Branch on this."""
    message: str
    """Message of the error body (for people, not for branching)."""
    data: dict[str, Any] | None
    """``data`` of the error body, for example ``{"reason": "example.com"}``, or None."""
    extra: dict[str, Any]
    """Fields the error body has next to ``code``, for example ``expected``, ``actual`` and
    ``currency`` of PRICE_CHANGED or ``fields`` of VALIDATION_FAILED."""
    request_id: str | None
    """X-Request-Id of the response. Quote it when you contact LETO about a failed call."""
    idempotency_key: str | None
    """Idempotency-Key sent with the request, or None."""
    response: ResponseInfo
    """Status and headers of the response."""
    body: dict[str, Any] | str
    """Decoded error body, or the raw text when the body was not JSON (for example a proxy page)."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int,
        error_code: str,
        data: dict[str, Any] | None,
        extra: dict[str, Any],
        response: ResponseInfo,
        body: dict[str, Any] | str,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.error_code = error_code
        self.data = data
        self.extra = extra
        self.response = response
        self.request_id = response.request_id
        self.idempotency_key = response.idempotency_key
        self.body = body
        self._read_details()

    def _read_details(self) -> None:
        """Hook for subclasses that read more fields from the error body."""

    @property
    def reason(self) -> str | None:
        """``data.reason`` of the error body (detail for this case), or None."""
        reason = (self.data or {}).get("reason")
        return reason if isinstance(reason, str) else None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(http_status={self.http_status}, error_code={self.error_code!r}, "
            f"message={self.message!r}, request_id={self.request_id!r})"
        )


class BadRequestError(LetoApiError):
    """400 or 422: the request was rejected as invalid (for example ``DOMAIN_INVALID_FORMAT``).

    Fix the request before sending it again.
    """


class ValidationError(BadRequestError):
    """400 ``VALIDATION_FAILED``: the body or query failed validation.

    ``fields`` lists ``path: rule`` entries when the API provides them.
    """

    fields: list[str]
    """Invalid fields as ``path: rule`` strings, for example ``period: must not be greater than 10``."""

    def _read_details(self) -> None:
        raw = self.extra.get("fields")
        self.fields = [f for f in raw if isinstance(f, str)] if isinstance(raw, list) else []


class IdempotencyKeyReusedError(BadRequestError):
    """422 ``IDEMPOTENCY_KEY_REUSED``: the key was already used for a different request.

    Use a new key for a new order.
    """


class AuthenticationError(LetoApiError):
    """401: the API key is missing, wrong, expired or revoked (``UNAUTHORIZED``)."""


class InsufficientBalanceError(LetoApiError):
    """The balance (plus usable credit) does not cover the charge. Nothing was charged.

    ``BALANCE_INSUFFICIENT``, ``BALANCE_NEGATIVE`` (402) or ``TRANSFER_INSUFFICIENT_BALANCE`` (400).
    """


class PermissionDeniedError(LetoApiError):
    """403: the API key is valid but not allowed to do this (``FORBIDDEN``, ``OPERATION_NOT_ALLOWED``)."""


class IpNotAllowedError(PermissionDeniedError):
    """403 ``IP_NOT_ALLOWED``: the API key has an IP allowlist and this request came from another address.

    Add the server's address to the key in the partner console.
    """


class NotFoundError(LetoApiError):
    """404: the domain, contact, zone or other resource does not exist in your account."""


class ConflictError(LetoApiError):
    """409: the request conflicts with the current state (for example ``DOMAIN_ALREADY_EXISTS``)."""


class PriceChangedError(ConflictError):
    """409 ``PRICE_CHANGED``: the ``expectedPrice`` you sent differs from the current total. Nothing was charged.

    Show the new price (``actual``) to whoever approved the old one, then send the request again
    with the new ``expectedPrice`` and a new Idempotency-Key.
    """

    expected: float | None
    """The ``expectedPrice`` you sent."""
    actual: float | None
    """The current total that would have been charged."""
    currency: str | None
    """Account currency of both amounts, for example ``KRW`` or ``USD``."""

    def _read_details(self) -> None:
        self.expected = _number(self.extra.get("expected"))
        self.actual = _number(self.extra.get("actual"))
        currency = self.extra.get("currency")
        self.currency = currency if isinstance(currency, str) else None


class IdempotencyInProgressError(ConflictError):
    """409 ``IDEMPOTENCY_REQUEST_IN_PROGRESS``: a request with the same key is still being processed.

    The SDK waits and retries this on its own; you see it only when the retries ran out. Send the
    same request with the same key again later.
    """

    retry_after: float | None
    """Seconds the API asked to wait (Retry-After), or None."""

    def _read_details(self) -> None:
        self.retry_after = parse_retry_after(self.response.header("Retry-After"))


class RateLimitError(LetoApiError):
    """429: too many requests (``RATE_LIMIT_EXCEEDED``, ``REGISTRY_RATE_LIMIT_EXCEEDED``).

    Raised after the SDK's own retries, or at once when Retry-After asks for a longer wait than
    the client's ``max_retry_after``.
    """

    retry_after: float | None
    """Seconds the API asked to wait (Retry-After), or None when it did not say."""

    def _read_details(self) -> None:
        self.retry_after = parse_retry_after(self.response.header("Retry-After"))


class ServerError(LetoApiError):
    """5xx: the API or the registry behind it failed (for example ``REGISTRY_CONNECTION_FAILED``).

    The SDK has already retried where that is safe. 501 (not supported for this TLD) is not a
    failure and comes as a plain LetoApiError.
    """


class ApiConnectionError(LetoError):
    """The API could not be reached or did not answer (DNS, TLS, connection or timeout error).

    Raised after the retries the SDK was allowed to make. For a request that charges the balance
    the outcome is unknown: it may or may not have been applied. Send it again later with the same
    ``idempotency_key``; the API then returns the first result instead of charging twice.
    The httpx exception is the ``__cause__``.
    """

    def __init__(self, message: str, *, method: str, path: str, attempts: int, idempotency_key: str | None) -> None:
        super().__init__(message)
        self.method = method
        """HTTP method of the request."""
        self.path = path
        """Path of the request, without the query string."""
        self.attempts = attempts
        """Number of attempts made, including retries."""
        self.idempotency_key = idempotency_key
        """Idempotency-Key sent with the request, or None when the request had none."""


class UnexpectedResponseError(LetoError):
    """A successful (2xx) response had a body the SDK could not read, for example HTML from a proxy."""

    def __init__(self, message: str, *, response: ResponseInfo, raw_body: str) -> None:
        super().__init__(message)
        self.response = response
        """Status, headers and X-Request-Id of the response."""
        self.raw_body = raw_body
        """Raw response body."""


class QuoteDeclinedError(LetoError):
    """The ``confirm`` callback of a ``..._with_quote()`` helper did not return True. Nothing was charged."""

    def __init__(self, quote: DomainQuoteDto) -> None:
        super().__init__("The quote was declined, so nothing was charged.")
        self.quote = quote
        """The quote that was declined (``data`` of GET /domain/quote)."""


# ---------------------------------------------------------------------------------------
# Error responses

_STANDARD_KEYS = frozenset({"code", "message", "data", "timestamp", "success"})

# Code per HTTP status when the body has none (for example an HTML page from a proxy).
# Same table domain-api uses for errors without a code of their own.
_CODE_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    402: "BALANCE_INSUFFICIENT",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE_ENTITY",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_SERVER_ERROR",
    501: "NOT_IMPLEMENTED",
    502: "REGISTRY_CONNECTION_FAILED",
    503: "SERVICE_UNAVAILABLE",
    504: "API_TIMEOUT",
}

# Codes with their own exception class, whatever the status.
_CLASS_BY_CODE: dict[str, type[LetoApiError]] = {
    "PRICE_CHANGED": PriceChangedError,
    "IDEMPOTENCY_REQUEST_IN_PROGRESS": IdempotencyInProgressError,
    "IDEMPOTENCY_KEY_REUSED": IdempotencyKeyReusedError,
    "VALIDATION_FAILED": ValidationError,
    "IP_NOT_ALLOWED": IpNotAllowedError,
    "BALANCE_INSUFFICIENT": InsufficientBalanceError,
    "BALANCE_NEGATIVE": InsufficientBalanceError,
    "TRANSFER_INSUFFICIENT_BALANCE": InsufficientBalanceError,
}


def error_class(status: int, code: str) -> type[LetoApiError]:
    """Exception class for an error response: by code first, then by status."""
    by_code = _CLASS_BY_CODE.get(code)
    if by_code is not None:
        return by_code
    if status in (400, 422):
        return BadRequestError
    if status == 401:
        return AuthenticationError
    if status == 402:
        return InsufficientBalanceError
    if status == 403:
        return PermissionDeniedError
    if status == 404:
        return NotFoundError
    if status == 409:
        return ConflictError
    if status == 429:
        return RateLimitError
    # 501 means "not supported for this TLD": nothing failed, so it is not a ServerError.
    if status == 501:
        return LetoApiError
    if status >= 500:
        return ServerError
    return LetoApiError


def error_from_response(response: httpx.Response, info: ResponseInfo) -> LetoApiError:
    """Turns an error response into the matching LetoApiError."""
    status = response.status_code
    decoded = decode_json(response.content)
    code = decoded.get("code") if isinstance(decoded, dict) else None
    if isinstance(decoded, dict) and isinstance(code, str) and code != "":
        raw_message = decoded.get("message")
        message = raw_message if isinstance(raw_message, str) and raw_message != "" else f"HTTP {status} ({code})"
        raw_data = decoded.get("data")
        data = {str(k): v for k, v in raw_data.items()} if isinstance(raw_data, dict) else None
        extra = {str(k): v for k, v in decoded.items() if k not in _STANDARD_KEYS}
        body: dict[str, Any] | str = {str(k): v for k, v in decoded.items()}
    else:
        code = _CODE_BY_STATUS.get(status, "UNKNOWN_ERROR")
        reason = response.reason_phrase
        message = f"HTTP {status}{' ' + reason if reason else ''} from the LETO Domain API (no error body)"
        data = None
        extra = {}
        body = response.text
    cls = error_class(status, code)
    return cls(message, http_status=status, error_code=code, data=data, extra=extra, response=info, body=body)


def decode_json(content: bytes) -> Any:
    """A decoded JSON object or array, or None when the content is empty, not JSON or a scalar."""
    if not content:
        return None
    try:
        value = json.loads(content)
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
