"""Page and auto_paginate() (PHP PaginationTest)."""

from __future__ import annotations

import math

import httpx
import pytest

from leto_domain_api import Page
from tests.conftest import AsyncClientFactory, ClientFactory
from tests.support import MockHttp, json_response, query


def domain_page(names: list[str], page: int, size: int, total: int) -> httpx.Response:
    """A list response in the common format: items in `data`, top-level `pagination`."""
    return json_response(
        200,
        {
            "timestamp": "2026-11-19T00:00:00.000Z",
            "success": True,
            "code": "DOMAIN_LIST_RETRIEVE_SUCCESS",
            "message": "ok",
            "data": [{"domainName": name} for name in names],
            "pagination": {"page": page, "size": size, "total": total, "totalPages": math.ceil(total / size)},
        },
    )


def transactions_page(ids: list[str], n: int) -> httpx.Response:
    return json_response(
        200,
        {
            "code": "SUCCESS",
            "message": "ok",
            "timestamp": "2026-11-19T00:00:00.000Z",
            "success": True,
            # `data.total` and friends are deprecated; the SDK reads the top-level pagination.
            "data": {
                "transactions": [{"id": i, "amount": "10.00"} for i in ids],
                "total": 3,
                "page": n,
                "limit": 2,
                "totalPages": 2,
            },
            "pagination": {"page": n, "size": 2, "total": 3, "totalPages": 2},
        },
    )


def test_auto_paginate_walks_every_page(http: MockHttp, client: ClientFactory) -> None:
    http.queue(
        domain_page(["a.com", "b.com"], 1, 2, 5),
        domain_page(["c.com", "d.com"], 2, 2, 5),
        domain_page(["e.com"], 3, 2, 5),
    )

    page = client().domains.list(query={"size": 2, "status": ["active"]})
    names = [domain["domainName"] for domain in page.auto_paginate()]

    assert names == ["a.com", "b.com", "c.com", "d.com", "e.com"]
    assert len(http.requests) == 3
    assert query(http.requests[0]) == {"size": "2", "status": "active"}
    assert query(http.requests[1]) == {"size": "2", "status": "active", "page": "2"}
    assert query(http.requests[2]) == {"size": "2", "status": "active", "page": "3"}


def test_pages_are_loaded_lazily(http: MockHttp, client: ClientFactory) -> None:
    http.queue(domain_page(["a.com", "b.com"], 1, 2, 4), domain_page(["c.com", "d.com"], 2, 2, 4))

    page = client().domains.list(query={"size": 2})
    for _ in page.auto_paginate():
        break

    assert len(http.requests) == 1
    assert http.remaining() == 1


def test_list_keeps_every_item(http: MockHttp, client: ClientFactory) -> None:
    http.queue(domain_page(["a.com", "b.com"], 1, 2, 3), domain_page(["c.com"], 2, 2, 3))

    everything = list(client().domains.list(query={"size": 2}).auto_paginate())

    assert [d["domainName"] for d in everything] == ["a.com", "b.com", "c.com"]


def test_page_exposes_items_and_body(http: MockHttp, client: ClientFactory) -> None:
    http.queue(domain_page(["a.com", "b.com"], 1, 2, 2))

    page = client().domains.list()

    assert isinstance(page, Page)
    assert len(page) == 2
    assert page.items[0]["domainName"] == "a.com"
    assert page.page_number == 1
    assert page.total == 2
    assert page.total_pages == 1
    assert page.pagination == {"page": 1, "size": 2, "total": 2, "totalPages": 1}
    assert page.has_next_page() is False
    assert page.next_page() is None
    assert page["code"] == "DOMAIN_LIST_RETRIEVE_SUCCESS"
    assert page.body["pagination"]["total"] == 2
    assert [domain["domainName"] for domain in page] == ["a.com", "b.com"]
    assert [p.page_number for p in page.pages()] == [1]
    assert "page_number=1" in repr(page)


def test_empty_list_stops(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {"data": [], "pagination": {"page": 1, "size": 20, "total": 0, "totalPages": 0}}))

    items = list(client().contacts.list().auto_paginate())

    assert items == []
    assert len(http.requests) == 1


def test_transactions_keep_items_in_data_transactions(http: MockHttp, client: ClientFactory) -> None:
    http.queue(transactions_page(["t1", "t2"], 1), transactions_page(["t3"], 2))

    first = client().transactions.list(query={"limit": 2, "type": "charge"})
    ids = [transaction["id"] for transaction in first.auto_paginate()]

    assert [t["id"] for t in first.items] == ["t1", "t2"]
    assert ids == ["t1", "t2", "t3"]
    assert query(http.requests[1]) == {"limit": "2", "type": "charge", "page": "2"}


def test_stops_when_the_server_ignores_the_page_number(http: MockHttp, client: ClientFactory) -> None:
    http.queue(domain_page(["a.com", "b.com"], 1, 2, 6), domain_page(["a.com", "b.com"], 1, 2, 6))

    names = [domain["domainName"] for domain in client().domains.list(query={"size": 2}).auto_paginate()]

    assert names == ["a.com", "b.com"]
    assert len(http.requests) == 2


def test_without_total_pages_a_full_page_means_more(http: MockHttp, client: ClientFactory) -> None:
    def page(names: list[str]) -> httpx.Response:
        return json_response(200, {"data": [{"domainName": n} for n in names], "pagination": {"size": 2}})

    http.queue(lambda request: page(["a.com", "b.com"]), lambda request: page(["c.com"]))

    everything = list(client().domains.list(query={"size": 2}).auto_paginate())

    assert len(everything) == 3
    assert query(http.requests[1]) == {"size": "2", "page": "2"}


def test_page_is_read_only(http: MockHttp, client: ClientFactory) -> None:
    http.queue(domain_page(["a.com"], 1, 20, 1))
    page = client().domains.list()

    with pytest.raises(TypeError):
        page["data"] = []  # type: ignore[index]
    page.items.clear()
    assert len(page.items) == 1, "items returns a copy"


def test_pagination_numbers_are_read_leniently(http: MockHttp, client: ClientFactory) -> None:
    http.queue(json_response(200, {"data": [{"domainName": "a.com"}], "pagination": {"page": "3", "size": 2.0}}))

    page = client().domains.list()

    assert page.pagination == {"page": 3, "size": 2}
    assert page.total_pages is None
    assert page.has_next_page() is False, "fewer items than the page size"


def test_missing_item_list_is_reported(http: MockHttp, client: ClientFactory) -> None:
    from leto_domain_api import UnexpectedResponseError

    http.queue(json_response(200, {"data": {"not": "a list"}, "pagination": {"page": 1}}))

    with pytest.raises(UnexpectedResponseError, match="no item list at data"):
        client().domains.list()


@pytest.mark.anyio
async def test_async_auto_paginate_walks_every_page_lazily(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(
        domain_page(["a.com", "b.com"], 1, 2, 5),
        domain_page(["c.com", "d.com"], 2, 2, 5),
        domain_page(["e.com"], 3, 2, 5),
    )

    page = await async_client().domains.list(query={"size": 2})
    names = [domain["domainName"] async for domain in page.auto_paginate()]

    assert names == ["a.com", "b.com", "c.com", "d.com", "e.com"]
    assert query(http.requests[2]) == {"size": "2", "page": "3"}
    assert [d["domainName"] for d in page] == ["a.com", "b.com"]


@pytest.mark.anyio
async def test_async_pages_stop_and_load_lazily(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(domain_page(["a.com", "b.com"], 1, 2, 6), domain_page(["a.com", "b.com"], 1, 2, 6))
    leto = async_client()

    page = await leto.domains.list(query={"size": 2})
    async for _ in page.auto_paginate():
        break
    assert len(http.requests) == 1

    pages = [p.page_number async for p in page.pages()]
    assert pages == [1], "the repeated page 1 is not returned"
    assert len(http.requests) == 2


@pytest.mark.anyio
async def test_async_transactions(http: MockHttp, async_client: AsyncClientFactory) -> None:
    http.queue(transactions_page(["t1", "t2"], 1), transactions_page(["t3"], 2))

    first = await async_client().transactions.list(query={"limit": 2})
    second = await first.next_page()

    assert second is not None
    assert [t["id"] for t in second.items] == ["t3"]
    assert await second.next_page() is None
