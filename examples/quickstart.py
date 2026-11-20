"""The README examples in one script.

mypy checks this file (make python-type), so the examples keep matching the SDK. Run it against a
sandbox account only:

    LETO_API_KEY=leto_sk_... python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys

from leto_domain_api import (
    ApiConnectionError,
    AsyncLetoClient,
    InsufficientBalanceError,
    LetoApiError,
    LetoClient,
    NotFoundError,
    PriceChangedError,
    RequestOptions,
    ServerError,
    generate_idempotency_key,
)
from leto_domain_api.types import CreateDomainDto, DomainQuoteDto


def main() -> None:
    with LetoClient(os.environ.get("LETO_API_KEY", ""), user_agent_suffix="quickstart/1.0") as client:
        # Balance
        status = client.account.get_status()
        if not status["isSandbox"]:
            sys.exit("Use a sandbox key for this example.")
        print("Balance:", status["balance"]["amount"], status["balance"]["currency"])

        # One TLD, then every TLD, loading the pages as you go
        com = client.pricing.get_tld("com")["data"][0]
        print(com["prices"]["registration"], com["currency"])
        for tld in client.pricing.list_tlds(query={"size": 100}).auto_paginate():
            print(f"{tld['tld']:<12} {tld['prices']['registration']:>10} {tld['currency']}")

        # Check, quote, register at the quoted price
        domain = f"example-{secrets.token_hex(3)}.com"
        contact_id = os.environ.get("LETO_CONTACT_ID", "")
        if client.domains.check(domain)["data"]["available"] and contact_id:
            quote = client.pricing.quote(domain, query={"period": 1})["data"]
            print(f"{domain} costs {quote['total']} {quote['currency']}")

            order_key = generate_idempotency_key()  # store it with your order before calling
            try:
                result = client.domains.register(
                    {
                        "domain": domain,
                        "period": 1,
                        "contact": {"registrant": contact_id, "administrative": contact_id},
                        "nameservers": ["ns1.example.net", "ns2.example.net"],
                        "expectedPrice": quote["total"],
                    },
                    options=RequestOptions(idempotency_key=order_key),
                )
                print("Registered, expires", result["data"]["expiresAt"])
            except PriceChangedError as e:
                print(f"Price changed from {e.expected} to {e.actual} {e.currency}, nothing charged")
            except InsufficientBalanceError:
                print("Top up the balance first")
            except ServerError as e:
                # API_TIMEOUT, NETWORK_ERROR, REGISTRY_CONNECTION_FAILED: not charged, registry outcome unknown.
                print(f"{e.error_code}: not charged; resending with the same key is safe")
            except LetoApiError as e:
                print(f"{e.error_code}: {e.message} (request {e.request_id or '-'})")
            except ApiConnectionError as e:
                print(f"Outcome unknown, retry later with key {e.idempotency_key or '-'}")

            # Or in one call: quote, confirm, register with expectedPrice
            body: CreateDomainDto = {
                "domain": domain,
                "period": 1,
                "contact": {"registrant": contact_id, "administrative": contact_id},
            }

            def cheap_enough(quote: DomainQuoteDto) -> bool:
                return quote["total"] <= 20.0

            client.domains.register_with_quote(body, cheap_enough)

        # Errors
        try:
            client.domains.renew("example.com", {"period": 1, "expectedPrice": 10.99})
        except PriceChangedError as e:
            print(f"Now {e.actual} {e.currency} instead of {e.expected}")
        except NotFoundError:
            print("No such domain in your account")
        except LetoApiError as e:
            print(f"{e.error_code}: {e.message} (request {e.request_id})")

        # Pages: domains, and transactions (kept in data.transactions)
        page = client.domains.list(query={"status": ["active", "expired"], "size": 100})
        print(page.total, "domains")
        for item in page.auto_paginate():
            print(item["domainName"], item["expiresAt"]["date"])
        for transaction in client.transactions.list(query={"type": "charge", "limit": 100}).auto_paginate():
            print(
                transaction["createdAt"], transaction["serviceAction"], transaction["amount"], transaction["currency"]
            )

        # Events you have not acknowledged yet
        events = client.events.list(query={"acknowledged": False, "size": 50})
        for event in events:
            print(event["createdAt"], event["type"], event["objectId"])
        if len(events) > 0:
            client.events.acknowledge_many({"ids": [event["id"] for event in events.items]})

        # Idempotency: your own key, stored with the order
        renewal_key = generate_idempotency_key()
        client.domains.renew("example.com", {"period": 1}, options=RequestOptions(idempotency_key=renewal_key))
        info = client.last_response
        if info is not None:
            print("Replayed:", info.replayed, "request", info.request_id, "attempts", info.attempts)

        # Per call options
        client.domains.get("example.com", options=RequestOptions(max_retries=0))


async def main_async() -> None:
    async with AsyncLetoClient(os.environ.get("LETO_API_KEY", "")) as client:
        status = await client.account.get_status()
        print(status["balance"]["amount"], status["balance"]["currency"])
        page = await client.domains.list(query={"size": 100})
        async for item in page.auto_paginate():
            print(item["domainName"])


if __name__ == "__main__":
    main()
    asyncio.run(main_async())
