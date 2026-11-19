"""Hand-written additions to the generated Domains and Transfers classes: order at a quoted price.

Each helper gets a quote (``GET /domain/quote``), passes it to ``confirm`` if given, then sends the
order with ``expectedPrice`` set to the quoted total. If the price changes in between (for example
a promotion ends), the API refuses the order with PriceChangedError and charges nothing.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from ._errors import InvalidArgumentError, QuoteDeclinedError
from ._options import RequestOptions
from ._transport import AsyncTransport, SyncTransport
from .resources.pricing import AsyncPricing, Pricing
from .types import (
    CreateDomainDto,
    DomainQuoteDto,
    DomainRegistrationResponseDto,
    DomainRenewalResponseDto,
    PricingQuoteQuery,
    RenewDomainDto,
    TransferInitiateResponseDto,
    TransferInRequestDto,
)

__all__ = ["AsyncDomainsHelpers", "AsyncTransfersHelpers", "DomainsHelpers", "TransfersHelpers"]

ConfirmQuote = Callable[[DomainQuoteDto], bool]
"""Called with the quote before anything is charged. Return True to place the order."""
AsyncConfirmQuote = Callable[[DomainQuoteDto], bool | Awaitable[bool]]
"""Like ConfirmQuote; on AsyncLetoClient it may also be a coroutine function."""


class _SyncDomains(Protocol):
    _transport: SyncTransport

    def register(
        self, body: CreateDomainDto, *, options: RequestOptions | None = None
    ) -> DomainRegistrationResponseDto: ...

    def renew(
        self, domain_name: str, body: RenewDomainDto, *, options: RequestOptions | None = None
    ) -> DomainRenewalResponseDto: ...


class _AsyncDomains(Protocol):
    _transport: AsyncTransport

    async def register(
        self, body: CreateDomainDto, *, options: RequestOptions | None = None
    ) -> DomainRegistrationResponseDto: ...

    async def renew(
        self, domain_name: str, body: RenewDomainDto, *, options: RequestOptions | None = None
    ) -> DomainRenewalResponseDto: ...


class _SyncTransfers(Protocol):
    _transport: SyncTransport

    def initiate(
        self, body: TransferInRequestDto, *, options: RequestOptions | None = None
    ) -> TransferInitiateResponseDto: ...


class _AsyncTransfers(Protocol):
    _transport: AsyncTransport

    async def initiate(
        self, body: TransferInRequestDto, *, options: RequestOptions | None = None
    ) -> TransferInitiateResponseDto: ...


def _check_body(body: Mapping[str, object]) -> None:
    if "expectedPrice" in body:
        raise InvalidArgumentError(
            "The ..._with_quote() helpers set expectedPrice from the quote. Call the plain method to send your own."
        )


def _registration_query(body: CreateDomainDto) -> PricingQuoteQuery:
    query: PricingQuoteQuery = {"action": "registration"}
    if body.get("period") is not None:
        query["period"] = body["period"]
    if body.get("localPresence") is True:
        query["localPresence"] = True
    return query


def _confirmed(quote: DomainQuoteDto, result: object) -> float:
    if result is not True:
        raise QuoteDeclinedError(quote)
    return quote["total"]


def _sync_price(transport: SyncTransport, domain: str, query: PricingQuoteQuery, confirm: ConfirmQuote | None) -> float:
    quote = Pricing(transport).quote(domain, query=query)["data"]
    return _confirmed(quote, True if confirm is None else confirm(quote))


async def _async_price(
    transport: AsyncTransport, domain: str, query: PricingQuoteQuery, confirm: AsyncConfirmQuote | None
) -> float:
    quote = (await AsyncPricing(transport).quote(domain, query=query))["data"]
    result: object = True
    if confirm is not None:
        result = confirm(quote)
        if inspect.isawaitable(result):
            result = await result
    return _confirmed(quote, result)


class DomainsHelpers:
    """Hand-written additions to the generated Domains class."""

    def register_with_quote(
        self: _SyncDomains,
        body: CreateDomainDto,
        confirm: ConfirmQuote | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> DomainRegistrationResponseDto:
        """Registers a domain at the price you were quoted.

        Gets a quote (``GET /domain/quote``), passes it to ``confirm`` if given, then calls
        ``register()`` with ``expectedPrice`` set to the quoted total. If the price changes in
        between, the API refuses the order with PriceChangedError and charges nothing.

        Args:
            body: The ``register()`` body, without expectedPrice. It is not changed.
            confirm: Return False to cancel before anything is charged (QuoteDeclinedError).
            options: Passed to ``register()``, for example your own Idempotency-Key.

        Returns:
            DomainRegistrationResponseDto: The ``register()`` response.

        Raises:
            PriceChangedError: The total changed after the quote. Nothing was charged.
            QuoteDeclinedError: ``confirm`` did not return True. Nothing was charged.
            LetoError: Any other failure.
        """
        _check_body(body)
        order = body.copy()
        order["expectedPrice"] = _sync_price(self._transport, body["domain"], _registration_query(body), confirm)
        return self.register(order, options=options)

    def renew_with_quote(
        self: _SyncDomains,
        domain_name: str,
        body: RenewDomainDto,
        confirm: ConfirmQuote | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> DomainRenewalResponseDto:
        """Renews a domain at the price you were quoted (add-ons attached to the domain included).

        Same flow as ``register_with_quote()``, for ``renew()``.

        Raises:
            PriceChangedError: The total changed after the quote. Nothing was charged.
            QuoteDeclinedError: ``confirm`` did not return True. Nothing was charged.
            LetoError: Any other failure.
        """
        _check_body(body)
        order = body.copy()
        query: PricingQuoteQuery = {"action": "renewal", "period": body["period"]}
        order["expectedPrice"] = _sync_price(self._transport, domain_name, query, confirm)
        return self.renew(domain_name, order, options=options)


class AsyncDomainsHelpers:
    """Hand-written additions to the generated AsyncDomains class."""

    async def register_with_quote(
        self: _AsyncDomains,
        body: CreateDomainDto,
        confirm: AsyncConfirmQuote | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> DomainRegistrationResponseDto:
        """Registers a domain at the price you were quoted. See ``Domains.register_with_quote()``.

        ``confirm`` may be a plain function or a coroutine function.
        """
        _check_body(body)
        order = body.copy()
        order["expectedPrice"] = await _async_price(self._transport, body["domain"], _registration_query(body), confirm)
        return await self.register(order, options=options)

    async def renew_with_quote(
        self: _AsyncDomains,
        domain_name: str,
        body: RenewDomainDto,
        confirm: AsyncConfirmQuote | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> DomainRenewalResponseDto:
        """Renews a domain at the price you were quoted. See ``Domains.renew_with_quote()``."""
        _check_body(body)
        order = body.copy()
        query: PricingQuoteQuery = {"action": "renewal", "period": body["period"]}
        order["expectedPrice"] = await _async_price(self._transport, domain_name, query, confirm)
        return await self.renew(domain_name, order, options=options)


class TransfersHelpers:
    """Hand-written additions to the generated Transfers class."""

    def initiate_with_quote(
        self: _SyncTransfers,
        body: TransferInRequestDto,
        confirm: ConfirmQuote | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> TransferInitiateResponseDto:
        """Requests a transfer in at the price you were quoted.

        Gets a transfer quote (``GET /domain/quote?action=transfer``), passes it to ``confirm`` if
        given, then calls ``initiate()`` with ``expectedPrice`` set to the quoted total.

        Raises:
            PriceChangedError: The total changed after the quote. Nothing was charged.
            QuoteDeclinedError: ``confirm`` did not return True. Nothing was charged.
            LetoError: Any other failure.
        """
        _check_body(body)
        order = body.copy()
        order["expectedPrice"] = _sync_price(self._transport, body["domainName"], {"action": "transfer"}, confirm)
        return self.initiate(order, options=options)


class AsyncTransfersHelpers:
    """Hand-written additions to the generated AsyncTransfers class."""

    async def initiate_with_quote(
        self: _AsyncTransfers,
        body: TransferInRequestDto,
        confirm: AsyncConfirmQuote | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> TransferInitiateResponseDto:
        """Requests a transfer in at the price you were quoted. See ``Transfers.initiate_with_quote()``."""
        _check_body(body)
        order = body.copy()
        order["expectedPrice"] = await _async_price(
            self._transport, body["domainName"], {"action": "transfer"}, confirm
        )
        return await self.initiate(order, options=options)
