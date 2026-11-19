"""register_with_quote(), renew_with_quote() and initiate_with_quote() (PHP QuoteHelperTest)."""

from __future__ import annotations

import httpx
import pytest

from leto_domain_api import InvalidArgumentError, PriceChangedError, QuoteDeclinedError, RequestOptions
from leto_domain_api.types import CreateDomainDto, DomainQuoteDto
from tests.conftest import AsyncClientFactory, ClientFactory
from tests.support import MockHttp, body, error, json_response, ok, query, raw_path


def quote(action: str, total: float, period: int = 1) -> httpx.Response:
    return json_response(
        200,
        {
            "code": "PRICING_RETRIEVE_SUCCESS",
            "message": "ok",
            "timestamp": "2026-11-19T00:00:00.000Z",
            "success": True,
            "data": {
                "domain": "example.kr",
                "tld": "kr",
                "action": action,
                "period": period,
                "currency": "KRW",
                "lines": [],
                "total": total,
                "promotions": [],
            },
        },
    )


def registration() -> CreateDomainDto:
    return {
        "domain": "example.kr",
        "period": 2,
        "contact": {"registrant": "c-1", "administrative": "c-1"},
        "localPresence": True,
    }


def test_register_with_quote_sends_the_quoted_total(http: MockHttp, client: ClientFactory) -> None:
    http.queue(quote("registration", 39000, 2), ok({"domainUuid": "d-1"}, 201))
    seen: list[DomainQuoteDto] = []

    def confirm(q: DomainQuoteDto) -> bool:
        seen.append(q)
        return q["total"] <= 40000

    order = registration()
    result = client().domains.register_with_quote(order, confirm, options=RequestOptions(idempotency_key="order-1"))

    assert result["data"]["domainUuid"] == "d-1"
    assert seen[0]["total"] == 39000
    assert order == registration(), "the caller's body is not changed"

    quote_request, register_request = http.requests
    assert quote_request.method == "GET"
    assert raw_path(quote_request) == "/api/v1/domain/quote"
    assert query(quote_request) == {
        "domain": "example.kr",
        "action": "registration",
        "period": "2",
        "localPresence": "true",
    }
    assert "Idempotency-Key" not in quote_request.headers

    assert register_request.method == "POST"
    assert raw_path(register_request) == "/api/v1/domain"
    assert register_request.headers["Idempotency-Key"] == "order-1"
    sent = body(register_request)
    assert sent["expectedPrice"] == 39000
    assert sent["domain"] == "example.kr"


def test_declined_quote_charges_nothing(http: MockHttp, client: ClientFactory) -> None:
    http.queue(quote("registration", 99000))

    with pytest.raises(QuoteDeclinedError) as caught:
        client().domains.register_with_quote(registration(), lambda q: False)

    assert caught.value.quote["total"] == 99000
    assert len(http.requests) == 1


def test_price_change_between_quote_and_order(http: MockHttp, client: ClientFactory) -> None:
    http.queue(
        quote("registration", 20000),
        error(
            409, "PRICE_CHANGED", "The price changed.", extra={"expected": 20000, "actual": 22000, "currency": "KRW"}
        ),
    )

    with pytest.raises(PriceChangedError) as caught:
        client().domains.register_with_quote(registration())

    assert caught.value.expected == 20000
    assert caught.value.actual == 22000
    assert caught.value.currency == "KRW"


def test_own_expected_price_is_refused(http: MockHttp, client: ClientFactory) -> None:
    order = registration()
    order["expectedPrice"] = 1.0

    with pytest.raises(InvalidArgumentError):
        client().domains.register_with_quote(order)
    assert http.requests == [], "refused before the quote"


def test_renew_with_quote(http: MockHttp, client: ClientFactory) -> None:
    http.queue(quote("renewal", 10.99, 3), ok({}, 201))

    client().domains.renew_with_quote("example.kr", {"period": 3})

    assert query(http.requests[0]) == {"domain": "example.kr", "action": "renewal", "period": "3"}
    assert raw_path(http.requests[1]) == "/api/v1/domain/example.kr/renewal"
    assert body(http.requests[1]) == {"period": 3, "expectedPrice": 10.99}


def test_initiate_transfer_with_quote(http: MockHttp, client: ClientFactory) -> None:
    http.queue(quote("transfer", 15000), ok({}, 201))

    client().transfers.initiate_with_quote(
        {"domainName": "example.kr", "authCode": "ABC123", "contacts": {"registrant": "c-1", "administrative": "c-1"}}
    )

    assert query(http.requests[0]) == {"domain": "example.kr", "action": "transfer"}
    assert raw_path(http.requests[1]) == "/api/v1/domain/transfer"
    assert body(http.requests[1])["expectedPrice"] == 15000
    assert http.requests[1].headers["Idempotency-Key"] != ""


@pytest.mark.anyio
async def test_async_register_with_quote_accepts_a_coroutine_confirm(
    http: MockHttp, async_client: AsyncClientFactory
) -> None:
    http.queue(quote("registration", 39000, 2), ok({"domainUuid": "d-1"}, 201))

    async def confirm(q: DomainQuoteDto) -> bool:
        return q["total"] <= 40000

    result = await async_client().domains.register_with_quote(registration(), confirm)

    assert result["data"]["domainUuid"] == "d-1"
    assert body(http.requests[1])["expectedPrice"] == 39000
    assert "Idempotency-Key" in http.requests[1].headers


@pytest.mark.anyio
async def test_async_declined_quote_and_other_helpers(http: MockHttp, async_client: AsyncClientFactory) -> None:
    leto = async_client()
    http.queue(quote("renewal", 10.99, 1))
    with pytest.raises(QuoteDeclinedError):
        await leto.domains.renew_with_quote("example.kr", {"period": 1}, lambda q: False)
    assert len(http.requests) == 1

    http.queue(quote("transfer", 15000), ok({}, 201))
    await leto.transfers.initiate_with_quote(
        {"domainName": "example.kr", "authCode": "A", "contacts": {"registrant": "c", "administrative": "c"}}
    )
    assert body(http.requests[2])["expectedPrice"] == 15000
