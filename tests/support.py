"""Mock HTTP answers and request readers shared by the tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl

import httpx

API_KEY = "leto_sk_0123456789abcdef0123456789abcdef0123456789abcdef"
UUID_V4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"

Answer = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response | Exception]


class MockHttp:
    """Answers requests from a queue (through httpx.MockTransport) and records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._queue: list[Answer] = []

    def queue(self, *answers: Answer) -> MockHttp:
        self._queue.extend(answers)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(f"Unexpected request {request.method} {request.url}")
        answer = self._queue.pop(0)
        if not isinstance(answer, (httpx.Response, Exception)):
            answer = answer(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def last(self) -> httpx.Request:
        if not self.requests:
            raise AssertionError("No request was sent.")
        return self.requests[-1]

    def remaining(self) -> int:
        return len(self._queue)


def json_response(status: int, body: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"Content-Type": "application/json", "X-Request-Id": f"req-{status}", **(headers or {})},
        content=json.dumps(body).encode(),
    )


def error(
    status: int,
    code: str,
    message: str = "Error",
    data: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """An error body in the documented format."""
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    body.update({"timestamp": "2026-11-19T00:00:00.000Z", "success": False})
    body.update(extra or {})
    return json_response(status, body, headers)


def ok(data: object = None, status: int = 200) -> httpx.Response:
    return json_response(
        status,
        {
            "timestamp": "2026-11-19T00:00:00.000Z",
            "success": True,
            "code": "SUCCESS",
            "message": "ok",
            "data": {} if data is None else data,
        },
    )


def proxy_error(status: int) -> httpx.Response:
    """An error page from a proxy or load balancer in front of the API (no API error body)."""
    return httpx.Response(
        status, headers={"Content-Type": "text/html"}, text=f"<html><body>{status} Gateway error</body></html>"
    )


def connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("Connection timed out")


def query(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.url.query.decode(), keep_blank_values=True))


def query_keys(request: httpx.Request) -> list[str]:
    return [key for key, _ in parse_qsl(request.url.query.decode(), keep_blank_values=True)]


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def raw_path(request: httpx.Request) -> str:
    return request.url.raw_path.split(b"?")[0].decode()
