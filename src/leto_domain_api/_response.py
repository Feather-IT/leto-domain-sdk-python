"""What the SDK keeps from an HTTP response besides the body."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ResponseInfo:
    """Status, headers, request ID and Idempotency-Key of one response.

    Get the last one with ``client.last_response``, or the one behind an error from
    ``error.response``.
    """

    status_code: int
    """HTTP status code."""
    headers: httpx.Headers
    """Response headers (names are case-insensitive)."""
    request_id: str | None
    """X-Request-Id of the response. Quote it when you contact LETO about a call."""
    idempotency_key: str | None
    """Idempotency-Key sent with the request, or None."""
    replayed: bool
    """True when the API returned the stored result of an earlier request with the same key."""
    attempts: int
    """Attempts made for this call, including retries."""

    @classmethod
    def from_response(cls, response: httpx.Response, idempotency_key: str | None, attempts: int) -> ResponseInfo:
        """Internal: built by the SDK for every response it receives."""
        request_id = response.headers.get("X-Request-Id")
        return cls(
            status_code=response.status_code,
            headers=httpx.Headers(response.headers),
            request_id=request_id or None,
            idempotency_key=idempotency_key,
            replayed=response.headers.get("Idempotent-Replayed", "").strip().lower() == "true",
            attempts=attempts,
        )

    def header(self, name: str) -> str | None:
        """A header value (several values joined by ", "), or None when the header is absent."""
        value: str | None = self.headers.get(name)
        return value
