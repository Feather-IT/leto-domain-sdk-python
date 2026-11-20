"""Sends requests for the generated resources: URL and headers, JSON, retries, errors.

Retries: a request is retried only when sending it twice cannot do harm, waiting as long as
Retry-After says or with exponential backoff.

- GET requests: connection errors, 429, 502, 503 and 504.
- Requests with an Idempotency-Key (they charge the balance): connection errors, 429, 503, and
  502/504 only when the response is not an error body of the API (a proxy or load balancer
  answered, so the API may still be running the first request and will answer the retry with
  409 or the stored result). A 502/504 error body from the API itself (for example API_TIMEOUT)
  means the registry call failed or timed out: the API has released the charge and forgotten the
  key, and the registry may still have applied the first request.
  Whether to send it again is left to the caller (the API makes it safe, see the README).
- A 409 IDEMPOTENCY_REQUEST_IN_PROGRESS (the first request with the same key is still running).

Other requests and other statuses (500, other 4xx) are not retried.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ._config import Config
from ._errors import (
    ApiConnectionError,
    InvalidArgumentError,
    LetoApiError,
    UnexpectedResponseError,
    decode_json,
    error_from_response,
)
from ._idempotency import generate_idempotency_key
from ._options import RequestOptions
from ._page import AsyncPage, Page
from ._response import ResponseInfo
from ._retry_after import parse_retry_after

__all__ = ["AsyncTransport", "SyncTransport"]

_RETRY_STATUSES = frozenset({429, 502, 503, 504})
# Statuses that mean the request was not processed, so a request with a key can be resent.
_NOT_PROCESSED_STATUSES = frozenset({429, 503})
# Gateway statuses that are retried for a request with a key only when a proxy sent them.
_GATEWAY_STATUSES = frozenset({502, 504})
_BACKOFF_BASE = 0.5
_BACKOFF_MAX = 8.0
# Transport errors after which the request may be sent again (the PSR-18 NetworkException of the
# PHP SDK): no connection, a timeout, or a connection closed without a response.
_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


async def _async_sleep(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class _Request:
    method: str
    path: str
    url: str
    headers: dict[str, str]
    content: bytes | None
    idempotency_key: str | None
    read: bool
    retry_safe: bool
    max_retries: int


class _BaseTransport:
    """Request building and retry rules shared by the sync and async transports."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.last_response: ResponseInfo | None = None

    def _prepare(
        self,
        method: str,
        path: str,
        path_params: Mapping[str, object] | None,
        query: Mapping[str, object] | None,
        body: object | None,
        options: RequestOptions | None,
        idempotent: bool,
    ) -> _Request:
        options = options or RequestOptions()
        idempotency_key: str | None = None
        if idempotent:
            idempotency_key = options.idempotency_key or generate_idempotency_key()
        elif options.idempotency_key is not None:
            raise InvalidArgumentError(
                f"{method} {path} does not accept an Idempotency-Key. Only requests that charge the balance do "
                "(registration, renewal, restore and transfer)."
            )
        # Sending twice is harmless for reads. Requests the API deduplicates by key are retried
        # under the stricter rules of _is_retryable().
        read = method in ("GET", "HEAD")
        url = self.config.base_url + _expand_path(path, path_params or {}) + _query_string(query or {})
        content = None if body is None else _encode_json(body)
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
            "User-Agent": self.config.user_agent,
        }
        if content is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        headers.update(options.headers)
        return _Request(
            method=method,
            path=path,
            url=url,
            headers=headers,
            content=content,
            idempotency_key=idempotency_key,
            read=read,
            retry_safe=read or idempotency_key is not None,
            max_retries=self.config.max_retries if options.max_retries is None else options.max_retries,
        )

    def _build(self, client: httpx.Client | httpx.AsyncClient, request: _Request) -> httpx.Request:
        if self.config.timeout is None:
            # The caller's own client keeps its timeouts.
            return client.build_request(request.method, request.url, content=request.content, headers=request.headers)
        return client.build_request(
            request.method, request.url, content=request.content, headers=request.headers, timeout=self.config.timeout
        )

    def _record(self, response: httpx.Response, request: _Request, attempt: int) -> ResponseInfo:
        info = ResponseInfo.from_response(response, request.idempotency_key, attempt)
        self.last_response = info
        return info

    @staticmethod
    def _connection_error(request: _Request, attempt: int, error: httpx.HTTPError) -> ApiConnectionError:
        if isinstance(error, _RETRYABLE_ERRORS):
            message = f"Could not reach the LETO Domain API ({request.method} {request.path}): {error}"
        else:
            message = f"The HTTP client could not send {request.method} {request.path}: {error}"
        return ApiConnectionError(
            message,
            method=request.method,
            path=request.path,
            attempts=attempt,
            idempotency_key=request.idempotency_key,
        )

    def _retry_delay(self, error: LetoApiError, request: _Request, attempt: int) -> float | None:
        """Seconds to wait before retrying after this error, or None when it is not retried."""
        if attempt > request.max_retries or not _is_retryable(error, request.read):
            return None
        retry_after = parse_retry_after(error.response.header("Retry-After"))
        if retry_after is not None:
            # A longer wait than allowed is left to the caller (RateLimitError.retry_after).
            return retry_after if retry_after <= self.config.max_retry_after else None
        return _backoff(attempt)

    @staticmethod
    def _decode(response: httpx.Response, info: ResponseInfo, request: _Request) -> Any:
        decoded = decode_json(response.content)
        if decoded is None:
            raise UnexpectedResponseError(
                f"{request.method} {request.path} returned HTTP {info.status_code} without a JSON body.",
                response=info,
                raw_body=response.text,
            )
        return decoded

    def _items(self, body: Any, items_path: Sequence[str], method: str, path: str) -> list[Any]:
        items: Any = body
        for key in items_path:
            items = items.get(key) if isinstance(items, dict) else None
        if items is None:
            items = []
        if not isinstance(items, list):
            info = self.last_response or ResponseInfo(200, httpx.Headers(), None, None, False, 1)
            raise UnexpectedResponseError(
                f"{method} {path} returned no item list at {'.'.join(items_path)}.",
                response=info,
                raw_body=json.dumps(body, ensure_ascii=False),
            )
        return items


class SyncTransport(_BaseTransport):
    """Internal: sends the requests of LetoClient."""

    def __init__(self, config: Config, client: httpx.Client) -> None:
        super().__init__(config)
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        body: object | None = None,
        options: RequestOptions | None = None,
        idempotent: bool = False,
    ) -> Any:
        """Sends a request and returns the decoded JSON body."""
        request = self._prepare(method, path, path_params, query, body, options, idempotent)
        response, info = self._send(request)
        return self._decode(response, info, request)

    def request_no_content(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        body: object | None = None,
        options: RequestOptions | None = None,
        idempotent: bool = False,
    ) -> None:
        """Sends a request whose response has no body (for example 204)."""
        self._send(self._prepare(method, path, path_params, query, body, options, idempotent))

    def request_page(
        self,
        method: str,
        path: str,
        *,
        items_path: Sequence[str],
        path_params: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Any, Any]:
        """Sends a list request and wraps the result in a Page that can load the following pages."""
        body = self.request(method, path, path_params=path_params, query=query, options=options)
        items = self._items(body, items_path, method, path)

        def fetch(next_query: dict[str, Any]) -> Page[Any, Any]:
            return self.request_page(
                method, path, items_path=items_path, path_params=path_params, query=next_query, options=options
            )

        return Page(body, items, query or {}, fetch)

    def _send(self, request: _Request) -> tuple[httpx.Response, ResponseInfo]:
        attempt = 0
        while True:
            attempt += 1
            http_request = self._build(self.client, request)
            try:
                response = self.client.send(http_request, follow_redirects=False)
            except _RETRYABLE_ERRORS as error:
                if request.retry_safe and attempt <= request.max_retries:
                    _sleep(_backoff(attempt))
                    continue
                raise self._connection_error(request, attempt, error) from error
            except httpx.HTTPError as error:
                raise self._connection_error(request, attempt, error) from error

            info = self._record(response, request, attempt)
            if response.is_success:
                return response, info
            api_error = error_from_response(response, info)
            delay = self._retry_delay(api_error, request, attempt)
            if delay is None:
                raise api_error
            _sleep(delay)


class AsyncTransport(_BaseTransport):
    """Internal: sends the requests of AsyncLetoClient."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        super().__init__(config)
        self.client = client

    async def request(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        body: object | None = None,
        options: RequestOptions | None = None,
        idempotent: bool = False,
    ) -> Any:
        """Sends a request and returns the decoded JSON body."""
        request = self._prepare(method, path, path_params, query, body, options, idempotent)
        response, info = await self._send(request)
        return self._decode(response, info, request)

    async def request_no_content(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        body: object | None = None,
        options: RequestOptions | None = None,
        idempotent: bool = False,
    ) -> None:
        """Sends a request whose response has no body (for example 204)."""
        await self._send(self._prepare(method, path, path_params, query, body, options, idempotent))

    async def request_page(
        self,
        method: str,
        path: str,
        *,
        items_path: Sequence[str],
        path_params: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        options: RequestOptions | None = None,
    ) -> AsyncPage[Any, Any]:
        """Sends a list request and wraps the result in an AsyncPage that can load the following pages."""
        body = await self.request(method, path, path_params=path_params, query=query, options=options)
        items = self._items(body, items_path, method, path)

        async def fetch(next_query: dict[str, Any]) -> AsyncPage[Any, Any]:
            return await self.request_page(
                method, path, items_path=items_path, path_params=path_params, query=next_query, options=options
            )

        return AsyncPage(body, items, query or {}, fetch)

    async def _send(self, request: _Request) -> tuple[httpx.Response, ResponseInfo]:
        attempt = 0
        while True:
            attempt += 1
            http_request = self._build(self.client, request)
            try:
                response = await self.client.send(http_request, follow_redirects=False)
            except _RETRYABLE_ERRORS as error:
                if request.retry_safe and attempt <= request.max_retries:
                    await _async_sleep(_backoff(attempt))
                    continue
                raise self._connection_error(request, attempt, error) from error
            except httpx.HTTPError as error:
                raise self._connection_error(request, attempt, error) from error

            info = self._record(response, request, attempt)
            if response.is_success:
                return response, info
            api_error = error_from_response(response, info)
            delay = self._retry_delay(api_error, request, attempt)
            if delay is None:
                raise api_error
            await _async_sleep(delay)


def _is_retryable(error: LetoApiError, read: bool) -> bool:
    """See the module docstring for the rules."""
    status = error.http_status
    if read:
        return status in _RETRY_STATUSES
    if error.idempotency_key is None:
        return False
    if error.error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS":
        return True
    if status in _NOT_PROCESSED_STATUSES:
        return True
    # A decoded body means the API answered with its own error, not a proxy.
    return status in _GATEWAY_STATUSES and not isinstance(error.body, dict)


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter: about 0.5 s, 1 s, 2 s, ... up to 8 s."""
    delay: float = min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** (attempt - 1)))
    return delay * (0.75 + random.random() * 0.5)


def _expand_path(path: str, params: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = params.get(name)
        if value is None or str(value) == "":
            raise InvalidArgumentError(f'{path} needs a non-empty "{name}".')
        return quote(str(value), safe="")

    return _PLACEHOLDER.sub(replace, path)


def _query_string(query: Mapping[str, object]) -> str:
    """Query string with booleans as true/false and lists joined by commas.

    Commas are the form the API documents for multi-value filters. None values are left out.
    """
    pairs: list[str] = []
    for name, value in query.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            text = ",".join(_scalar(name, item) for item in value)
        else:
            text = _scalar(name, value)
        pairs.append(f"{quote(str(name), safe='')}={quote(text, safe='')}")
    return "?" + "&".join(pairs) if pairs else ""


def _scalar(name: str, value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(int(value)) if value.is_integer() else repr(value)
    raise InvalidArgumentError(f'Query parameter "{name}" must be a string, number or boolean, or a list of them.')


def _encode_json(body: object) -> bytes:
    try:
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise InvalidArgumentError(f"The request body cannot be encoded as JSON: {error}") from error
