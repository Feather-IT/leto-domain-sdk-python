"""Requests the client sends: authentication, User-Agent, URL, query, body, options (PHP ClientTest)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
import pytest

from leto_domain_api import (
    AsyncLetoClient,
    InvalidArgumentError,
    LetoClient,
    LetoError,
    RequestOptions,
    UnexpectedResponseError,
    version,
)
from leto_domain_api.resources import Dns, Domains
from tests.conftest import ClientFactory
from tests.support import API_KEY, MockHttp, body, json_response, ok, query, query_keys, raw_path


def test_sends_the_api_key_as_bearer_token(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {"tenantId": "t-1", "status": "ACTIVE"}))

    status = client().account.get_status()

    assert status["tenantId"] == "t-1"
    request = http.last
    assert request.method == "GET"
    assert str(request.url) == "https://domain-api.leto.kr/api/v1/account/status"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Accept"] == "application/json"
    assert "X-Service-Token" not in request.headers
    assert "Idempotency-Key" not in request.headers


def test_user_agent_has_the_sdk_version_and_the_suffix(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {}), json_response(200, {}))

    client().account.get_status()
    assert http.last.headers["User-Agent"].startswith(f"leto-domain-sdk-python/{version.SDK} python/")

    client(user_agent_suffix="my-panel/1.4").account.get_status()
    assert http.last.headers["User-Agent"].endswith(" my-panel/1.4")


def test_base_url_is_normalized(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {}), json_response(200, {}))

    client(base_url="http://localhost:4000/").account.get_status()
    assert str(http.last.url) == "http://localhost:4000/api/v1/account/status"

    client(base_url="https://sandbox.example/api/v1").account.get_status()
    assert str(http.last.url) == "https://sandbox.example/api/v1/account/status"


def test_path_parameters_are_encoded(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok(), ok())

    client().domains.whois("한글.kr")
    assert raw_path(http.last) == "/api/v1/domain/" + quote("한글.kr", safe="") + "/whois"

    client().dns.delete_record("zone/1", "rec 2")
    assert raw_path(http.last) == "/api/v1/dns/zones/zone%2F1/records/rec%202"


def test_empty_path_parameter_is_rejected_before_sending(http: MockHttp, client: ClientFactory) -> None:
    with pytest.raises(InvalidArgumentError) as caught:
        client().domains.get("")

    assert isinstance(caught.value, LetoError)
    assert isinstance(caught.value, ValueError)
    assert "domainName" in str(caught.value)
    assert http.requests == []


def test_query_values_are_encoded(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {"data": [], "pagination": {"page": 1, "size": 50, "total": 0, "totalPages": 0}}))

    client().domains.list(query={"status": ["active", "expired"], "page": 2, "size": 50, "search": "shop & co"})

    assert http.last.url.query == b"status=active%2Cexpired&page=2&size=50&search=shop%20%26%20co"


def test_query_values_none_bool_and_float(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok(), ok())

    client().pricing.quote("example.kr", query={"period": 2.0, "localPresence": True, "action": None})  # type: ignore[arg-type]
    assert query(http.last) == {"domain": "example.kr", "period": "2", "localPresence": "true"}

    client().pricing.quote("example.kr", query={"period": 1.5})
    assert query(http.last)["period"] == "1.5"


def test_query_value_of_another_type_is_rejected(http: MockHttp, client: ClientFactory) -> None:
    with pytest.raises(InvalidArgumentError, match="search"):
        client().domains.list(query={"search": {"a": 1}})  # type: ignore[arg-type]
    assert http.requests == []


def test_required_query_parameters_are_arguments(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok({"domain": "example.kr", "available": True}), ok({"total": 10}))

    result = client().domains.check("example.kr")
    assert result["data"]["available"] is True
    assert query(http.last) == {"domain": "example.kr"}

    client().pricing.quote("example.kr", query={"action": "renewal", "period": 3, "localPresence": False})
    assert query(http.last) == {"domain": "example.kr", "action": "renewal", "period": "3", "localPresence": "false"}
    assert query_keys(http.last)[0] == "domain"


def test_json_body_is_sent(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok({"id": "r1"}, 201))

    client().dns.create_record("zone-1", {"name": "www", "type": "A", "target": "203.0.113.10", "ttl": 300})

    request = http.last
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/json"
    assert body(request) == {"name": "www", "type": "A", "target": "203.0.113.10", "ttl": 300}


def test_body_keeps_unicode_and_rejects_what_json_cannot_hold(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok({}, 201))

    client().dns.create_record("zone-1", {"name": "한글", "type": "TXT", "target": "v=spf1", "ttl": 300})
    assert "한글".encode() in http.last.content

    with pytest.raises(InvalidArgumentError, match="JSON"):
        client().dns.create_record("zone-1", {"name": "www", "type": "A", "target": "x", "ttl": float("nan")})
    assert len(http.requests) == 1


def test_request_without_body_sends_no_content_type(http: MockHttp, client: ClientFactory) -> None:
    http.queue(ok())

    client().transfers.approve("tr-1")

    request = http.last
    assert request.method == "POST"
    assert "Content-Type" not in request.headers
    assert request.content == b""


def test_no_content_response(http: MockHttp, client: ClientFactory) -> None:
    http.queue(httpx.Response(204))
    leto = client()

    result = leto.contacts.delete("c-1")  # type: ignore[func-returns-value]

    assert result is None
    assert http.last.method == "DELETE"
    assert leto.last_response is not None
    assert leto.last_response.status_code == 204


def test_extra_headers_are_passed(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {}))

    client().account.get_status(options=RequestOptions(headers={"X-Trace": "abc"}))

    assert http.last.headers["X-Trace"] == "abc"


@pytest.mark.parametrize(
    "headers",
    [{"Authorization": "Bearer other"}, {"idempotency-key": "k"}, {"Bad Name": "x"}, {"X-Ok": "a\r\nX-Evil: 1"}],
)
def test_reserved_or_invalid_headers_are_refused(headers: dict[str, str]) -> None:
    with pytest.raises(InvalidArgumentError):
        RequestOptions(headers=headers)


def test_last_response_keeps_the_request_id(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {}, {"X-Request-Id": "abc-123"}))
    assert client().last_response is None
    leto = client()

    leto.account.get_status()

    info = leto.last_response
    assert info is not None
    assert info.request_id == "abc-123"
    assert info.header("x-request-id") == "abc-123"
    assert info.status_code == 200
    assert info.attempts == 1
    assert info.replayed is False


def test_non_json_success_body_is_reported(http: MockHttp, client: ClientFactory) -> None:
    http.queue(httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html>proxy</html>"))

    with pytest.raises(UnexpectedResponseError) as caught:
        client().account.get_status()

    assert caught.value.raw_body == "<html>proxy</html>"
    assert caught.value.response.status_code == 200


def test_resources_are_created_once(client: ClientFactory) -> None:
    leto = client()

    assert isinstance(leto.domains, Domains)
    assert leto.domains is leto.domains
    assert isinstance(leto.dns, Dns)
    assert isinstance(leto.domain_security, object)


def test_api_key_is_not_printed(client: ClientFactory) -> None:
    leto = client()

    for text in (repr(leto), str(leto), repr(leto._transport.config), repr(vars(leto))):
        assert API_KEY not in text
    assert "leto_sk_0123..." in repr(leto)


@pytest.mark.parametrize(
    ("api_key", "options", "error"),
    [
        pytest.param("", {}, InvalidArgumentError, id="empty key"),
        pytest.param("leto_sk_abc\nX-Evil: 1", {}, InvalidArgumentError, id="key with newline"),
        pytest.param(API_KEY, {"timout": 5}, TypeError, id="unknown option"),
        pytest.param(API_KEY, {"max_retries": -1}, InvalidArgumentError, id="negative retries"),
        pytest.param(API_KEY, {"max_retries": True}, InvalidArgumentError, id="bool retries"),
        pytest.param(API_KEY, {"max_retry_after": 0}, InvalidArgumentError, id="zero max retry after"),
        pytest.param(API_KEY, {"timeout": -1}, InvalidArgumentError, id="negative timeout"),
        pytest.param(API_KEY, {"base_url": "domain-api.leto.kr"}, InvalidArgumentError, id="bad base URL"),
        pytest.param(API_KEY, {"base_url": "https://x.example/?a=1"}, InvalidArgumentError, id="base URL with query"),
        pytest.param(API_KEY, {"user_agent_suffix": "panel\n"}, InvalidArgumentError, id="suffix with newline"),
        pytest.param(API_KEY, {"http_client": httpx.AsyncClient()}, TypeError, id="async client for sync client"),
    ],
)
def test_invalid_options_are_rejected(api_key: str, options: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        LetoClient(api_key, **options)


def test_async_client_refuses_a_sync_http_client() -> None:
    with pytest.raises(TypeError):
        AsyncLetoClient(API_KEY, http_client=httpx.Client())  # type: ignore[arg-type]


def test_timeouts(http: MockHttp) -> None:
    def timeouts(leto: LetoClient) -> dict[str, float | None]:
        http.queue(json_response(200, {}))
        leto.account.get_status()
        return dict(http.last.extensions["timeout"])

    mock = httpx.MockTransport(http.handler)
    # Default: 60 seconds, 10 to connect, sent with every request.
    assert timeouts(LetoClient(API_KEY, http_client=httpx.Client(transport=mock), timeout=None)) == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }, "a client passed in keeps its own timeouts (httpx default 5 s)"
    assert timeouts(LetoClient(API_KEY, http_client=httpx.Client(transport=mock), timeout=30)) == {
        "connect": 10.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }, "a timeout given together with a client is applied to each request"
    own = LetoClient(API_KEY)
    assert own._transport.client.timeout == httpx.Timeout(60.0, connect=10.0)
    own.close()


def test_closing_closes_only_its_own_http_client(http: MockHttp) -> None:
    passed_in = httpx.Client(transport=httpx.MockTransport(http.handler))
    with LetoClient(API_KEY, http_client=passed_in) as leto:
        assert isinstance(leto, LetoClient)
    assert not passed_in.is_closed

    with LetoClient(API_KEY) as own:
        inner = own._transport.client
    assert inner.is_closed
