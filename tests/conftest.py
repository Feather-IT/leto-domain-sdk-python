from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from leto_domain_api import AsyncLetoClient, LetoClient, _transport
from tests.support import API_KEY, MockHttp

ClientFactory = Callable[..., LetoClient]
AsyncClientFactory = Callable[..., AsyncLetoClient]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def http() -> MockHttp:
    return MockHttp()


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Seconds the SDK asked to sleep between retries, in order (nothing really sleeps)."""
    recorded: list[float] = []

    async def async_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(_transport, "_sleep", recorded.append)
    monkeypatch.setattr(_transport, "_async_sleep", async_sleep)
    return recorded


@pytest.fixture
def client(http: MockHttp, sleeps: list[float]) -> ClientFactory:
    """Makes a LetoClient whose requests go to `http`."""

    def factory(**options: Any) -> LetoClient:
        options.setdefault("http_client", httpx.Client(transport=httpx.MockTransport(http.handler)))
        return LetoClient(API_KEY, **options)

    return factory


@pytest.fixture
def async_client(http: MockHttp, sleeps: list[float]) -> AsyncClientFactory:
    """Makes an AsyncLetoClient whose requests go to `http`."""

    def factory(**options: Any) -> AsyncLetoClient:
        options.setdefault("http_client", httpx.AsyncClient(transport=httpx.MockTransport(http.handler)))
        return AsyncLetoClient(API_KEY, **options)

    return factory
