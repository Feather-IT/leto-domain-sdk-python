"""Retries, backoff and Retry-After (PHP RetryTest)."""

from __future__ import annotations

from email.utils import formatdate

import httpx
import pytest

from leto_domain_api import ApiConnectionError, RequestOptions, ServerError
from leto_domain_api._retry_after import parse_retry_after
from tests.conftest import AsyncClientFactory, ClientFactory
from tests.support import MockHttp, body, connect_error, error, json_response, ok


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_get_is_retried_on_transient_status(
    http: MockHttp, client: ClientFactory, sleeps: list[float], status: int
) -> None:
    http.queue(error(status, "SERVICE_UNAVAILABLE"), json_response(200, {"tenantId": "t-1"}))
    leto = client()

    result = leto.account.get_status()

    assert result["tenantId"] == "t-1"
    assert len(http.requests) == 2
    assert len(sleeps) == 1
    # First backoff is 0.5 s with +-25 % jitter.
    assert 0.375 <= sleeps[0] <= 0.625
    assert leto.last_response is not None
    assert leto.last_response.attempts == 2


def test_retry_after_seconds_are_honored(http: MockHttp, client: ClientFactory, sleeps: list[float]) -> None:
    http.queue(error(429, "RATE_LIMIT_EXCEEDED", headers={"Retry-After": "3"}), json_response(200, {}))

    client().account.get_status()

    assert sleeps == [3.0]


def test_retry_after_http_date_is_honored() -> None:
    now = 1_795_000_000

    assert parse_retry_after(formatdate(now + 7, usegmt=True), now) == 7.0
    assert parse_retry_after(formatdate(now - 30, usegmt=True), now) == 0.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after(" 2 ") == 2.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None


def test_retry_after_date_is_used_between_attempts(
    http: MockHttp, client: ClientFactory, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_795_000_000.0
    monkeypatch.setattr("leto_domain_api._retry_after.time.time", lambda: now)
    http.queue(error(503, "SERVICE_UNAVAILABLE", headers={"Retry-After": formatdate(now + 4, usegmt=True)}), ok())

    client().account.get_status()

    assert sleeps == [4.0]


def test_backoff_grows_and_retries_run_out(http: MockHttp, client: ClientFactory, sleeps: list[float]) -> None:
    http.queue(
        error(503, "SERVICE_UNAVAILABLE"),
        error(503, "SERVICE_UNAVAILABLE"),
        error(503, "SERVICE_UNAVAILABLE", headers={"X-Request-Id": "last"}),
    )

    with pytest.raises(ServerError) as caught:
        client().account.get_status()

    assert caught.value.request_id == "last"
    assert caught.value.response.attempts == 3
    assert len(http.requests) == 3
    assert len(sleeps) == 2
    assert 0.75 <= sleeps[1] <= 1.25


def test_max_retries_can_be_changed_per_client_and_per_call(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(503, "SERVICE_UNAVAILABLE"))
    with pytest.raises(ServerError):
        client(max_retries=0).account.get_status()
    assert len(http.requests) == 1

    http.queue(error(503, "SERVICE_UNAVAILABLE"), error(503, "SERVICE_UNAVAILABLE"), json_response(200, {}))
    client(max_retries=0).account.get_status(options=RequestOptions(max_retries=2))
    assert len(http.requests) == 4


def test_post_without_idempotency_key_is_not_retried(
    http: MockHttp, client: ClientFactory, sleeps: list[float]
) -> None:
    http.queue(error(503, "SERVICE_UNAVAILABLE"))

    with pytest.raises(ServerError):
        client().dns.create_zone({"domainName": "example.com"})

    assert len(http.requests) == 1
    assert sleeps == []


def test_internal_server_error_is_not_retried(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(500, "UNKNOWN_ERROR"))

    with pytest.raises(ServerError):
        client().account.get_status()

    assert len(http.requests) == 1


@pytest.mark.parametrize(
    "failure",
    [connect_error(), httpx.ReadTimeout("read timed out"), httpx.RemoteProtocolError("closed without response")],
    ids=["connect", "read timeout", "closed connection"],
)
def test_connection_error_on_get_is_retried(
    http: MockHttp, client: ClientFactory, sleeps: list[float], failure: Exception
) -> None:
    http.queue(failure, json_response(200, {"tenantId": "t-1"}))

    result = client().account.get_status()

    assert result["tenantId"] == "t-1"
    assert len(http.requests) == 2
    assert len(sleeps) == 1


def test_connection_error_on_post_without_key_is_not_retried(http: MockHttp, client: ClientFactory) -> None:
    http.queue(connect_error())

    with pytest.raises(ApiConnectionError) as caught:
        client().dns.create_zone({"domainName": "example.com"})

    e = caught.value
    assert e.method == "POST"
    assert e.path == "/api/v1/dns/zones"
    assert e.attempts == 1
    assert e.idempotency_key is None
    assert isinstance(e.__cause__, httpx.ConnectError)
    assert len(http.requests) == 1


def test_connection_errors_run_out(http: MockHttp, client: ClientFactory) -> None:
    http.queue(connect_error(), connect_error(), connect_error())

    with pytest.raises(ApiConnectionError) as caught:
        client().account.get_status()

    assert caught.value.attempts == 3


def test_non_network_client_error_is_not_retried(http: MockHttp, client: ClientFactory) -> None:
    http.queue(httpx.UnsupportedProtocol("bad request object"))

    with pytest.raises(ApiConnectionError, match="could not send"):
        client().account.get_status()

    assert len(http.requests) == 1


def test_retried_request_keeps_its_body(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(503, "SERVICE_UNAVAILABLE"), ok({"domainUuid": "d-1"}, 201))

    client().domains.register(
        {"domain": "example.com", "period": 1, "contact": {"registrant": "c-1", "administrative": "c-1"}}
    )

    assert len(http.requests) == 2
    assert body(http.requests[0]) == body(http.requests[1])
    assert body(http.requests[1])["domain"] == "example.com"


def test_mock_answers_are_what_the_tests_expect() -> None:
    # Guards the test helpers: a connection error is an httpx network error, a 204 has no body.
    assert isinstance(connect_error(), httpx.NetworkError)
    assert httpx.Response(204).status_code == 204
    assert httpx.Response(204).content == b""


@pytest.mark.anyio
async def test_async_get_is_retried_after_status_and_connection_errors(
    http: MockHttp, async_client: AsyncClientFactory, sleeps: list[float]
) -> None:
    http.queue(
        error(503, "SERVICE_UNAVAILABLE"),
        connect_error(),
        json_response(200, {"tenantId": "t-1"}),
    )
    leto = async_client()

    result = await leto.account.get_status()

    assert result["tenantId"] == "t-1"
    assert len(sleeps) == 2
    assert leto.last_response is not None
    assert leto.last_response.attempts == 3


@pytest.mark.anyio
async def test_async_retries_run_out_and_post_is_not_retried(
    http: MockHttp, async_client: AsyncClientFactory, sleeps: list[float]
) -> None:
    http.queue(connect_error(), connect_error(), connect_error())
    with pytest.raises(ApiConnectionError) as caught:
        await async_client().account.get_status()
    assert caught.value.attempts == 3

    http.queue(error(503, "SERVICE_UNAVAILABLE"))
    with pytest.raises(ServerError):
        await async_client().dns.create_zone({"domainName": "example.com"})
    assert len(http.requests) == 4
    assert len(sleeps) == 2
