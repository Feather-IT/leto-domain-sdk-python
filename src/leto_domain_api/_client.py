"""LetoClient and AsyncLetoClient."""

from __future__ import annotations

from types import TracebackType

import httpx
from typing_extensions import Self

from . import version
from ._config import DEFAULT_MAX_RETRIES, DEFAULT_MAX_RETRY_AFTER, build_config, masked_key
from ._response import ResponseInfo
from ._transport import AsyncTransport, SyncTransport
from .resources._accessors import Accessors, AsyncAccessors

__all__ = ["AsyncLetoClient", "LetoClient"]


class LetoClient(Accessors):
    """Client for the LETO Domain API.

    ```python
    from leto_domain_api import LetoClient

    client = LetoClient("leto_sk_...")
    status = client.account.get_status()
    print(status["balance"]["amount"], status["balance"]["currency"])
    ```

    Operations are grouped by resource (``client.domains``, ``client.dns``, ``client.transfers``,
    ...), one per section of the API reference. See OPERATIONS.md for the full list.

    The client holds an ``httpx.Client`` with a connection pool. Create one and reuse it, and
    close it when you are done (``client.close()`` or ``with LetoClient(...) as client:``).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = version.DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
        user_agent_suffix: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Creates a client.

        Args:
            api_key: API key from the partner console (``leto_sk_...``). Sent as
                ``Authorization: Bearer <key>``.
            base_url: API origin, default https://domain-api.leto.kr. A trailing ``/api/v1`` is removed.
            timeout: Seconds to wait for each network operation (connect at most 10), or an
                ``httpx.Timeout``. Default 60. With your own ``http_client`` its timeouts apply
                unless you pass this as well.
            max_retries: Retries for GET requests and requests with an Idempotency-Key, default 2.
            max_retry_after: Longest Retry-After (seconds) the SDK waits for by itself, default 60.
            user_agent_suffix: Appended to the User-Agent, for example "my-panel/1.4".
            http_client: Your own ``httpx.Client`` (proxies, certificates, event hooks). The SDK
                does not close it.
        """
        if http_client is not None and not isinstance(http_client, httpx.Client):
            raise TypeError("http_client must be an httpx.Client (use AsyncLetoClient for an httpx.AsyncClient).")
        config = build_config(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            max_retry_after=max_retry_after,
            user_agent_suffix=user_agent_suffix,
            own_client=http_client is None,
        )
        self._owns_client = http_client is None
        client = http_client if http_client is not None else httpx.Client(timeout=config.timeout)
        self._transport = SyncTransport(config, client)

    @property
    def last_response(self) -> ResponseInfo | None:
        """Status, headers, X-Request-Id and Idempotency-Key of the last response, or None before the first call."""
        return self._transport.last_response

    def close(self) -> None:
        """Closes the connection pool, unless the httpx client was passed in."""
        if self._owns_client:
            self._transport.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        config = self._transport.config
        return f"LetoClient(api_key={masked_key(config.api_key)!r}, base_url={config.base_url!r})"


class AsyncLetoClient(AsyncAccessors):
    """Client for the LETO Domain API with asyncio.

    ```python
    from leto_domain_api import AsyncLetoClient

    async with AsyncLetoClient("leto_sk_...") as client:
        status = await client.account.get_status()
    ```

    Same resources, methods and options as LetoClient; every method is a coroutine and list
    methods return an AsyncPage.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = version.DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
        user_agent_suffix: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Creates a client. The options are those of LetoClient; ``http_client`` is an ``httpx.AsyncClient``."""
        if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
            raise TypeError("http_client must be an httpx.AsyncClient (use LetoClient for an httpx.Client).")
        config = build_config(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            max_retry_after=max_retry_after,
            user_agent_suffix=user_agent_suffix,
            own_client=http_client is None,
        )
        self._owns_client = http_client is None
        client = http_client if http_client is not None else httpx.AsyncClient(timeout=config.timeout)
        self._transport = AsyncTransport(config, client)

    @property
    def last_response(self) -> ResponseInfo | None:
        """Status, headers, X-Request-Id and Idempotency-Key of the last response, or None before the first call.

        With concurrent calls on one client this is whichever response arrived last; read
        ``error.response`` of an exception instead when it matters.
        """
        return self._transport.last_response

    async def aclose(self) -> None:
        """Closes the connection pool, unless the httpx client was passed in."""
        if self._owns_client:
            await self._transport.client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        config = self._transport.config
        return f"AsyncLetoClient(api_key={masked_key(config.api_key)!r}, base_url={config.base_url!r})"
