"""Error responses become exceptions (PHP ErrorMappingTest, examples from domain-api docs/ERROR_CODES.md)."""

from __future__ import annotations

import httpx
import pytest

from leto_domain_api import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyReusedError,
    InsufficientBalanceError,
    IpNotAllowedError,
    LetoApiError,
    LetoError,
    NotFoundError,
    PermissionDeniedError,
    PriceChangedError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from tests.conftest import ClientFactory
from tests.support import MockHttp, error


def test_documented_error_body_becomes_an_exception(http: MockHttp, client: ClientFactory) -> None:
    http.queue(
        error(
            404,
            "DOMAIN_NOT_FOUND",
            "The requested domain could not be found.",
            {"reason": "example.com"},
            headers={"X-Request-Id": "7f1c0a52-1d2e-4c3b-9a8d-0e1f2a3b4c5d"},
        )
    )

    with pytest.raises(NotFoundError) as caught:
        client().domains.get("example.com")

    e = caught.value
    assert isinstance(e, LetoApiError)
    assert isinstance(e, LetoError)
    assert e.http_status == 404
    assert e.error_code == "DOMAIN_NOT_FOUND"
    assert e.message == "The requested domain could not be found."
    assert str(e) == "The requested domain could not be found."
    assert e.data == {"reason": "example.com"}
    assert e.reason == "example.com"
    assert e.extra == {}
    assert e.request_id == "7f1c0a52-1d2e-4c3b-9a8d-0e1f2a3b4c5d"
    assert e.idempotency_key is None
    assert isinstance(e.body, dict)
    assert e.body["success"] is False
    assert "DOMAIN_NOT_FOUND" in repr(e)


def test_price_changed_has_the_amounts(http: MockHttp, client: ClientFactory) -> None:
    http.queue(
        error(
            409,
            "PRICE_CHANGED",
            "The price changed. Nothing was charged.",
            extra={"expected": 10.99, "actual": 12.5, "currency": "USD"},
        )
    )

    with pytest.raises(PriceChangedError) as caught:
        client().domains.renew("example.com", {"period": 1, "expectedPrice": 10.99})

    e = caught.value
    assert isinstance(e, ConflictError)
    assert e.error_code == "PRICE_CHANGED"
    assert e.expected == 10.99
    assert e.actual == 12.5
    assert e.currency == "USD"
    assert e.extra == {"expected": 10.99, "actual": 12.5, "currency": "USD"}
    assert e.idempotency_key is not None, "renew() sends an Idempotency-Key"
    # A 409 is not retried.
    assert len(http.requests) == 1


def test_validation_failed_lists_the_fields(http: MockHttp, client: ClientFactory) -> None:
    http.queue(
        error(
            400,
            "VALIDATION_FAILED",
            "Validation failed",
            extra={"fields": ["period: must not be greater than 10", "domain: should not be empty"]},
        )
    )

    with pytest.raises(ValidationError) as caught:
        client().domains.register({"domain": "", "period": 11, "contact": {"registrant": "c", "administrative": "c"}})

    assert isinstance(caught.value, BadRequestError)
    assert caught.value.fields == ["period: must not be greater than 10", "domain: should not be empty"]


def test_rate_limit_has_retry_after(http: MockHttp, client: ClientFactory, sleeps: list[float]) -> None:
    http.queue(error(429, "RATE_LIMIT_EXCEEDED", "Too many requests.", headers={"Retry-After": "120"}))

    # 120 s is longer than max_retry_after (60 s), so the SDK does not wait.
    with pytest.raises(RateLimitError) as caught:
        client().account.get_status()

    assert caught.value.retry_after == 120.0
    assert sleeps == []


@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        pytest.param(400, "BAD_REQUEST", BadRequestError, id="generic 400"),
        pytest.param(400, "DOMAIN_INVALID_PERIOD", BadRequestError, id="domain 400"),
        pytest.param(401, "UNAUTHORIZED", AuthenticationError, id="unauthorized"),
        pytest.param(402, "BALANCE_INSUFFICIENT", InsufficientBalanceError, id="balance"),
        pytest.param(402, "BALANCE_NEGATIVE", InsufficientBalanceError, id="negative balance"),
        pytest.param(400, "TRANSFER_INSUFFICIENT_BALANCE", InsufficientBalanceError, id="transfer balance (400)"),
        pytest.param(403, "FORBIDDEN", PermissionDeniedError, id="forbidden"),
        pytest.param(403, "IP_NOT_ALLOWED", IpNotAllowedError, id="ip not allowed"),
        pytest.param(404, "CONTACT_NOT_FOUND", NotFoundError, id="not found"),
        pytest.param(409, "DOMAIN_ALREADY_EXISTS", ConflictError, id="already exists"),
        pytest.param(409, "DNS_CNAME_CONFLICT", ConflictError, id="dns conflict"),
        pytest.param(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", IdempotencyInProgressError, id="idempotency in progress"),
        pytest.param(422, "IDEMPOTENCY_KEY_REUSED", IdempotencyKeyReusedError, id="idempotency reused"),
        pytest.param(400, "IDEMPOTENCY_KEY_INVALID", BadRequestError, id="idempotency invalid"),
        pytest.param(410, "DOMAIN_EXPIRED", LetoApiError, id="gone"),
        pytest.param(422, "UNPROCESSABLE_ENTITY", BadRequestError, id="unprocessable"),
        pytest.param(500, "UNKNOWN_ERROR", ServerError, id="server"),
        pytest.param(501, "DNSSEC_NOT_SUPPORTED", LetoApiError, id="not supported"),
        pytest.param(502, "CONTACT_UPDATE_REGISTRY_SYNC_FAILED", ServerError, id="registry"),
        pytest.param(503, "REGISTRY_MAINTENANCE_MODE", ServerError, id="maintenance"),
    ],
)
def test_code_and_status_pick_the_class(
    http: MockHttp, client: ClientFactory, status: int, code: str, cls: type[LetoApiError]
) -> None:
    # A POST without Idempotency-Key is never retried, so one response is enough.
    http.queue(error(status, code))

    with pytest.raises(LetoApiError) as caught:
        client().contacts.create(
            {
                "name": {"national": "홍길동", "international": "Gildong Hong"},
                "email": "owner@example.com",
                "phone": "+82.212345678",
                "address": {
                    "postalCode": "06236",
                    "national": {"state": "서울특별시", "city": "강남구", "street": "테헤란로 1", "detail": "1층"},
                    "international": {"state": "Seoul", "city": "Gangnam-gu", "street": "1 Teheran-ro", "detail": "1F"},
                },
                "country": "KR",
            }
        )

    assert type(caught.value) is cls
    assert caught.value.error_code == code
    assert caught.value.http_status == status


def test_extra_fields_are_kept(http: MockHttp, client: ClientFactory) -> None:
    http.queue(error(400, "JOB_BATCH_TOO_LARGE", "Too many instances.", extra={"limit": 1000}))

    with pytest.raises(BadRequestError) as caught:
        client().jobs.create({"commands": []})

    assert caught.value.extra == {"limit": 1000}


def test_error_without_json_body_uses_the_status_code(http: MockHttp, client: ClientFactory) -> None:
    http.queue(httpx.Response(502, headers={"Content-Type": "text/html"}, text="<html>Bad Gateway</html>"))

    with pytest.raises(ServerError) as caught:
        client(max_retries=0).account.get_status()

    e = caught.value
    assert e.http_status == 502
    assert e.error_code == "REGISTRY_CONNECTION_FAILED"
    assert e.body == "<html>Bad Gateway</html>"
    assert e.data is None
    assert "HTTP 502" in str(e)


def test_json_body_without_code_counts_as_no_error_body(http: MockHttp, client: ClientFactory) -> None:
    http.queue(httpx.Response(418, headers={"Content-Type": "application/json"}, content=b'{"error":"teapot"}'))

    with pytest.raises(LetoApiError) as caught:
        client().account.get_status()

    assert type(caught.value) is LetoApiError
    assert caught.value.error_code == "UNKNOWN_ERROR"
    assert caught.value.body == '{"error":"teapot"}'
