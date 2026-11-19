"""Idempotency-Key on the requests that charge the balance (PHP IdempotencyTest)."""

from __future__ import annotations

import re

import pytest

from leto_domain_api import (
    ApiConnectionError,
    IdempotencyInProgressError,
    InvalidArgumentError,
    RequestOptions,
    ServerError,
    generate_idempotency_key,
    is_valid_idempotency_key,
)
from leto_domain_api.types import CreateDomainDto
from tests.conftest import AsyncClientFactory, ClientFactory
from tests.support import UUID_V4, MockHttp, connect_error, error, json_response, ok, proxy_error


def registration() -> CreateDomainDto:
    return {"domain": "example.com", "period": 1, "contact": {"registrant": "c-1", "administrative": "c-1"}}


def test_money_requests_get_a_generated_key(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok({}, 201), ok({}, 201), ok({}, 201), ok({}, 201))
    leto = client()

    leto.domains.register(registration())
    leto.domains.renew("example.com", {"period": 1})
    leto.domains.restore("example.com")
    leto.transfers.initiate(
        {"domainName": "example.com", "authCode": "ABC123", "contacts": {"registrant": "c-1", "administrative": "c-1"}}
    )

    keys = [request.headers["Idempotency-Key"] for request in http.requests]
    for key in keys:
        assert re.fullmatch(UUID_V4, key)
    assert len(set(keys)) == 4, "every call gets its own key"
    assert leto.last_response is not None
    assert leto.last_response.idempotency_key == keys[3]


def test_caller_key_is_used(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok({}, 201))

    client().domains.register(registration(), options=RequestOptions(idempotency_key="order-2026-0001"))

    assert http.last.headers["Idempotency-Key"] == "order-2026-0001"


def test_retry_after_proxy_error_sends_the_same_key(http: MockHttp, client: ClientFactory) -> None:
    http.queue(proxy_error(502), proxy_error(504), ok({"domainUuid": "d-1"}, 201))

    result = client().domains.register(registration())

    assert result["data"]["domainUuid"] == "d-1"
    assert len(http.requests) == 3
    assert len({request.headers["Idempotency-Key"] for request in http.requests}) == 1


def test_retry_after_not_processed_status_sends_the_same_key(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(503, "REGISTRY_MAINTENANCE_MODE"), error(429, "REGISTRY_RATE_LIMIT_EXCEEDED"), ok({}, 201))

    client().domains.renew("example.com", {"period": 1}, options=RequestOptions(idempotency_key="renew-1"))

    assert len(http.requests) == 3
    assert http.requests[2].headers["Idempotency-Key"] == "renew-1"


@pytest.mark.parametrize(
    ("status", "code"),
    [(502, "REGISTRY_CONNECTION_FAILED"), (502, "NETWORK_ERROR"), (504, "API_TIMEOUT")],
)
def test_registry_failure_from_the_api_is_not_retried(
    http: MockHttp, client: ClientFactory, sleeps: list[float], status: int, code: str
) -> None:
    """The API answered that the registry call failed or timed out.

    It released the charge and forgot the key, and the registry may have applied the request (a
    renewal twice), so the SDK does not resend it.
    """
    http.queue(error(status, code))

    with pytest.raises(ServerError) as caught:
        client().domains.renew("example.com", {"period": 1}, options=RequestOptions(idempotency_key="renew-2"))

    assert caught.value.error_code == code
    assert caught.value.idempotency_key == "renew-2"
    assert len(http.requests) == 1
    assert sleeps == []


def test_registry_failure_on_a_read_is_retried(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(504, "API_TIMEOUT"), ok({"available": True}))

    client().domains.check("example.com")

    assert len(http.requests) == 2


def test_retry_after_connection_error_sends_the_same_key(http: MockHttp, client: ClientFactory) -> None:
    http.queue(connect_error(), ok({}, 201))

    client().domains.renew("example.com", {"period": 2}, options=RequestOptions(idempotency_key="renew-42"))

    assert len(http.requests) == 2
    assert http.requests[0].headers["Idempotency-Key"] == "renew-42"
    assert http.requests[1].headers["Idempotency-Key"] == "renew-42"


def test_request_in_progress_is_retried_with_the_same_key(
    http: MockHttp, client: ClientFactory, sleeps: list[float]
) -> None:
    http.queue(
        error(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", headers={"Retry-After": "2"}),
        ok({"domainUuid": "d-1"}, 201),
    )

    client().domains.register(registration(), options=RequestOptions(idempotency_key="order-7"))

    assert sleeps == [2.0]
    assert http.requests[1].headers["Idempotency-Key"] == "order-7"


def test_request_in_progress_surfaces_when_retries_run_out(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", headers={"Retry-After": "2"}))

    with pytest.raises(IdempotencyInProgressError) as caught:
        client(max_retries=0).domains.register(registration(), options=RequestOptions(idempotency_key="order-8"))

    assert caught.value.retry_after == 2.0
    assert caught.value.idempotency_key == "order-8"


def test_replayed_response_is_flagged(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(201, {"success": True, "data": {}}, {"Idempotent-Replayed": "true"}))
    leto = client()

    leto.domains.register(registration(), options=RequestOptions(idempotency_key="order-9"))

    assert leto.last_response is not None
    assert leto.last_response.replayed is True


def test_unknown_outcome_keeps_the_key_for_a_later_retry(http: MockHttp, client: ClientFactory) -> None:
    http.queue(connect_error(), connect_error(), connect_error())

    with pytest.raises(ApiConnectionError) as caught:
        client().domains.register(registration())

    e = caught.value
    assert e.attempts == 3
    assert e.idempotency_key is not None
    assert re.fullmatch(UUID_V4, e.idempotency_key)
    assert http.requests[2].headers["Idempotency-Key"] == e.idempotency_key


def test_key_is_refused_where_the_api_ignores_it(http: MockHttp, client: ClientFactory) -> None:
    with pytest.raises(InvalidArgumentError, match="does not accept an Idempotency-Key"):
        client().dns.create_zone({"domainName": "example.com"}, options=RequestOptions(idempotency_key="k-1"))

    assert http.requests == []


@pytest.mark.parametrize("key", ["has space", "", "a" * 256, "tab\tkey", "한글"])
def test_invalid_key_is_refused(key: str) -> None:
    with pytest.raises(InvalidArgumentError):
        RequestOptions(idempotency_key=key)


def test_generated_keys_are_valid_and_unique() -> None:
    keys = {generate_idempotency_key() for _ in range(50)}

    assert len(keys) == 50
    assert all(is_valid_idempotency_key(key) and re.fullmatch(UUID_V4, key) for key in keys)
    assert not is_valid_idempotency_key("a" * 256)
    assert not is_valid_idempotency_key("")
    assert not is_valid_idempotency_key("key\n")


def test_caller_body_is_not_changed_by_the_request(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok({}, 201))
    order = registration()

    client().domains.register(order)

    assert order == registration()


@pytest.mark.anyio
async def test_async_retry_sends_the_same_key(
    http: MockHttp, async_client: AsyncClientFactory, sleeps: list[float]
) -> None:
    http.queue(
        connect_error(),
        error(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", headers={"Retry-After": "2"}),
        proxy_error(504),
        ok({"domainUuid": "d-1"}, 201),
    )

    result = await async_client(max_retries=3).domains.register(registration())

    assert result["data"]["domainUuid"] == "d-1"
    keys = {request.headers["Idempotency-Key"] for request in http.requests}
    assert len(http.requests) == 4
    assert len(keys) == 1
    assert re.fullmatch(UUID_V4, keys.pop())
    assert sleeps[1] == 2.0


@pytest.mark.anyio
async def test_async_registry_failure_is_not_retried(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(error(504, "API_TIMEOUT"))

    with pytest.raises(ServerError) as caught:
        await async_client().domains.renew("example.com", {"period": 1})

    assert caught.value.error_code == "API_TIMEOUT"
    assert len(http.requests) == 1


@pytest.mark.anyio
async def test_async_key_is_refused_where_the_api_ignores_it(http: MockHttp, async_client: AsyncClientFactory) -> None:
    with pytest.raises(InvalidArgumentError):
        await async_client().dns.create_zone(
            {"domainName": "example.com"}, options=RequestOptions(idempotency_key="k-1")
        )
    assert http.requests == []
