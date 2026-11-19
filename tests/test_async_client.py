"""AsyncLetoClient: the core paths of the sync tests, awaited."""

from __future__ import annotations

import httpx
import pytest

from leto_domain_api import AsyncLetoClient, NotFoundError, RequestOptions, UnexpectedResponseError, version
from leto_domain_api.resources import AsyncDomains
from tests.conftest import AsyncClientFactory
from tests.support import API_KEY, MockHttp, body, error, json_response, ok, query


@pytest.mark.anyio
async def test_sends_key_user_agent_query_and_body(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(ok({"available": True}), ok({"id": "r1"}, 201))
    leto = async_client(user_agent_suffix="my-panel/1.4", base_url="https://sandbox.example/api/v1/")

    result = await leto.domains.check("example.kr")
    assert result["data"]["available"] is True
    request = http.last
    assert str(request.url) == "https://sandbox.example/api/v1/domain/check?domain=example.kr"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["User-Agent"].startswith(f"leto-domain-sdk-python/{version.SDK} python/")
    assert request.headers["User-Agent"].endswith(" my-panel/1.4")

    await leto.dns.create_record(
        "zone-1",
        {"name": "www", "type": "A", "target": "203.0.113.10", "ttl": 300},
        options=RequestOptions(headers={"X-Trace": "abc"}),
    )
    assert body(http.last) == {"name": "www", "type": "A", "target": "203.0.113.10", "ttl": 300}
    assert http.last.headers["X-Trace"] == "abc"
    assert query(http.last) == {}


@pytest.mark.anyio
async def test_errors_and_last_response(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(error(404, "DOMAIN_NOT_FOUND", "Not found.", {"reason": "example.com"}, headers={"X-Request-Id": "r-9"}))
    leto = async_client()

    with pytest.raises(NotFoundError) as caught:
        await leto.domains.get("example.com")

    assert caught.value.error_code == "DOMAIN_NOT_FOUND"
    assert caught.value.reason == "example.com"
    assert caught.value.request_id == "r-9"
    assert leto.last_response is not None
    assert leto.last_response.status_code == 404


@pytest.mark.anyio
async def test_no_content_and_unexpected_body(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(httpx.Response(204), httpx.Response(200, text="<html>proxy</html>"))
    leto = async_client()

    assert await leto.contacts.delete("c-1") is None  # type: ignore[func-returns-value]
    with pytest.raises(UnexpectedResponseError):
        await leto.account.get_status()


@pytest.mark.anyio
async def test_resources_and_closing(http: MockHttp) -> None:
    passed_in = httpx.AsyncClient(transport=httpx.MockTransport(http.handler))
    async with AsyncLetoClient(API_KEY, http_client=passed_in) as leto:
        assert isinstance(leto.domains, AsyncDomains)
        assert leto.domains is leto.domains
        assert API_KEY not in repr(leto)
    assert not passed_in.is_closed
    await passed_in.aclose()

    async with AsyncLetoClient(API_KEY) as own:
        inner = own._transport.client
        http.queue(json_response(200, {}))
    assert inner.is_closed
