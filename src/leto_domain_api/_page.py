"""Pages of the list endpoints."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from typing import Any, Generic, TypeVar, cast

from typing_extensions import TypedDict

__all__ = ["AsyncPage", "Page", "PaginationInfo"]

ItemT = TypeVar("ItemT")
BodyT = TypeVar("BodyT")


class PaginationInfo(TypedDict, total=False):
    """The ``pagination`` object of a list response, with the values the SDK could read as integers."""

    page: int
    size: int
    total: int
    totalPages: int


class _BasePage(Generic[ItemT, BodyT]):
    def __init__(self, body: BodyT, items: list[ItemT], query: Mapping[str, Any]) -> None:
        self._body = body
        self._items = items
        self._query = dict(query)

    @property
    def items(self) -> list[ItemT]:
        """Items on this page (a new list on every call)."""
        return list(self._items)

    @property
    def body(self) -> BodyT:
        """The whole response body."""
        return self._body

    @property
    def pagination(self) -> PaginationInfo:
        """The ``pagination`` object of the response: ``page``, ``size``, ``total``, ``totalPages``."""
        raw = self._body.get("pagination") if isinstance(self._body, Mapping) else None
        if not isinstance(raw, Mapping):
            return PaginationInfo()
        values = {key: _integer(raw.get(key)) for key in ("page", "size", "total", "totalPages")}
        return cast(PaginationInfo, {key: value for key, value in values.items() if value is not None})

    @property
    def page_number(self) -> int:
        """Number of this page, from 1."""
        page = self.pagination.get("page")
        number = _number(page if page is not None else self._query.get("page", 1))
        return max(1, int(number)) if number is not None else 1

    @property
    def total(self) -> int | None:
        """Items over all pages, or None when the response does not say."""
        return self.pagination.get("total")

    @property
    def total_pages(self) -> int | None:
        """Number of pages, or None when the response does not say."""
        return self.pagination.get("totalPages")

    def has_next_page(self) -> bool:
        """True when another page follows (from ``totalPages``, or a full page when that is missing)."""
        if not self._items:
            return False
        total_pages = self.total_pages
        if total_pages is not None:
            return self.page_number < total_pages
        size: Any = self.pagination.get("size")
        if size is None:
            size = self._query.get("size")
        if size is None:
            size = self._query.get("limit")
        number = _number(size)
        return number is not None and len(self._items) >= int(number)

    def _next_query(self) -> dict[str, Any]:
        return {**self._query, "page": self.page_number + 1}

    def _is_repeat(self, following: _BasePage[ItemT, BodyT]) -> bool:
        # Stop instead of looping if the server ignored the page number.
        return bool(following.pagination) and following.page_number <= self.page_number

    def __iter__(self) -> Iterator[ItemT]:
        """The items of this page only. Use ``auto_paginate()`` for every page."""
        return iter(list(self._items))

    def __len__(self) -> int:
        """Items on this page."""
        return len(self._items)

    def __getitem__(self, key: str) -> Any:
        """A field of the response body, for example ``page["code"]``."""
        return cast(Mapping[str, Any], self._body)[key]

    def __repr__(self) -> str:
        return f"{type(self).__name__}(page_number={self.page_number}, items={len(self._items)}, total={self.total})"


class Page(_BasePage[ItemT, BodyT]):
    """One page of a list endpoint.

    ```python
    page = client.domains.list(query={"size": 100})
    for domain in page.auto_paginate():   # every item of every page
        print(domain["domainName"])
    ```

    Iterating the page itself gives the items of this page only. The whole response body is
    ``page.body``, and ``page["pagination"]`` reads a field of it.

    The items are where the endpoint keeps them: ``data`` for most lists, ``data.transactions``
    for ``GET /transactions``. The page number is taken from the top-level ``pagination`` object.
    """

    def __init__(
        self,
        body: BodyT,
        items: list[ItemT],
        query: Mapping[str, Any],
        fetch: Callable[[dict[str, Any]], Page[ItemT, BodyT]],
    ) -> None:
        """Internal: pages are created by the SDK."""
        super().__init__(body, items, query)
        self._fetch = fetch

    def next_page(self) -> Page[ItemT, BodyT] | None:
        """Loads the next page with the same query, or returns None on the last page."""
        if not self.has_next_page():
            return None
        following = self._fetch(self._next_query())
        return None if self._is_repeat(following) else following

    def pages(self) -> Iterator[Page[ItemT, BodyT]]:
        """This page and the pages after it, loaded one at a time as you iterate."""
        page: Page[ItemT, BodyT] | None = self
        while page is not None:
            yield page
            page = page.next_page()

    def auto_paginate(self) -> Iterator[ItemT]:
        """Every item from this page on, loading later pages as you iterate."""
        for page in self.pages():
            yield from page._items


class AsyncPage(_BasePage[ItemT, BodyT]):
    """One page of a list endpoint, from AsyncLetoClient.

    ```python
    page = await client.domains.list(query={"size": 100})
    async for domain in page.auto_paginate():   # every item of every page
        print(domain["domainName"])
    ```

    Iterating the page itself (``for item in page``) gives the items of this page only.
    """

    def __init__(
        self,
        body: BodyT,
        items: list[ItemT],
        query: Mapping[str, Any],
        fetch: Callable[[dict[str, Any]], Awaitable[AsyncPage[ItemT, BodyT]]],
    ) -> None:
        """Internal: pages are created by the SDK."""
        super().__init__(body, items, query)
        self._fetch = fetch

    async def next_page(self) -> AsyncPage[ItemT, BodyT] | None:
        """Loads the next page with the same query, or returns None on the last page."""
        if not self.has_next_page():
            return None
        following = await self._fetch(self._next_query())
        return None if self._is_repeat(following) else following

    async def pages(self) -> AsyncIterator[AsyncPage[ItemT, BodyT]]:
        """This page and the pages after it, loaded one at a time as you iterate."""
        page: AsyncPage[ItemT, BodyT] | None = self
        while page is not None:
            yield page
            page = await page.next_page()

    async def auto_paginate(self) -> AsyncIterator[ItemT]:
        """Every item from this page on, loading later pages as you iterate."""
        async for page in self.pages():
            for item in page._items:
                yield item


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None
