# LETO Domain API for Python

Python client for the [LETO Domain API](https://partners.leto.kr/docs): register, renew and
transfer domains, manage DNS, contacts, forwarding and more from your own platform, billed to
your LETO partner balance.

- Every API operation as a typed method (`client.domains.register(...)`), generated from the
  API's OpenAPI document. See [OPERATIONS.md](OPERATIONS.md) for the full list.
- A synchronous client (`LetoClient`) and an asyncio client (`AsyncLetoClient`) with the same
  methods.
- Errors as exceptions with the API's stable error `code`.
- Safe retries, automatic `Idempotency-Key` on requests that charge your balance.
- Page iteration over every list endpoint.
- `TypedDict` request and response shapes for mypy, pyright and your editor (`py.typed`).

## Requirements

- Python 3.10 or later
- [httpx](https://www.python-httpx.org) 0.27 or later (installed with the package)

## Installation

```bash
pip install leto-domain-api
```

## Quick start

Create an API key in the partner console (it starts with `leto_sk_`). Keep it on the server.

```python
import os

from leto_domain_api import LetoClient

client = LetoClient(os.environ["LETO_API_KEY"])

status = client.account.get_status()
print(status["balance"]["amount"], status["balance"]["currency"])
```

Responses are the decoded JSON bodies (plain dicts), as documented in the API reference. Most
have the standard envelope `{ code, message, data, timestamp, success }`; the result is in
`data`.

The client keeps a connection pool. Create one, reuse it, and close it when you are done
(`client.close()`, or `with LetoClient(...) as client:`).

### Method arguments

```
method(path parameters..., required query parameters..., body, *, query=None, options=None)
```

Path parameters, required query parameters and the body are positional, in that order. Optional
query parameters go in the `query` dict, with the parameter names of the API reference
(`{"size": 100}`). `options` is a `RequestOptions` for that one call.

### Async

`AsyncLetoClient` has the same resources and methods as coroutines.

```python
import asyncio
import os

from leto_domain_api import AsyncLetoClient


async def main() -> None:
    async with AsyncLetoClient(os.environ["LETO_API_KEY"]) as client:
        status = await client.account.get_status()
        print(status["balance"]["amount"], status["balance"]["currency"])

        page = await client.domains.list(query={"size": 100})
        async for domain in page.auto_paginate():
            print(domain["domainName"])


asyncio.run(main())
```

### TLD prices

```python
# One TLD
com = client.pricing.get_tld("com")["data"][0]
print(com["prices"]["registration"], com["currency"])

# Every TLD, loading the pages as you go
for tld in client.pricing.list_tlds(query={"size": 100}).auto_paginate():
    print(tld["tld"], tld["prices"]["registration"], tld["currency"])
```

### Quote, then register at that price

Prices can change (a promotion ends, a price list is updated). Get a quote, show it, and send
its `total` as `expectedPrice`. If the price changed in the meantime, the API refuses the order
with `PRICE_CHANGED` and charges nothing.

```python
from leto_domain_api import RequestOptions

if client.domains.check("example.com")["data"]["available"]:
    quote = client.pricing.quote("example.com", query={"period": 1})["data"]
    # Show quote["total"] and quote["currency"] to your customer, then:

    result = client.domains.register(
        {
            "domain": "example.com",
            "period": 1,
            "contact": {"registrant": contact_id, "administrative": contact_id},
            "nameservers": ["ns1.example.net", "ns2.example.net"],
            "expectedPrice": quote["total"],
        },
        options=RequestOptions(idempotency_key=order.idempotency_key),
    )
    print("Expires", result["data"]["expiresAt"])
```

The same in one call, with an optional check before anything is charged:

```python
result = client.domains.register_with_quote(
    body,                                    # the register() body without expectedPrice
    lambda quote: quote["total"] <= max_price,
)
```

`renew_with_quote(domain_name, body)` and `client.transfers.initiate_with_quote(body)` work the
same way. When the callback does not return `True`, `QuoteDeclinedError` is raised and nothing
is charged. On the async client the callback may also be a coroutine function.

Contacts are created first with `client.contacts.create({...})`; use the `id` from the response
as `registrant` and `administrative`.

### Errors

Every API error is a `LetoApiError`. Branch on `e.error_code` (stable, only ever added to), not
on the message. Common cases have their own subclasses.

```python
from leto_domain_api import (
    ApiConnectionError,
    InsufficientBalanceError,
    LetoApiError,
    NotFoundError,
    PriceChangedError,
)

try:
    client.domains.renew("example.com", {"period": 1, "expectedPrice": 10.99})
except PriceChangedError as e:
    # Nothing was charged. Ask again with the new price.
    print(f"Now {e.actual} {e.currency} instead of {e.expected}")
except InsufficientBalanceError:
    print("Top up your balance")
except NotFoundError:
    print("No such domain in your account")
except LetoApiError as e:
    # e.http_status, e.error_code, e.message, e.data, e.extra
    print(f"{e.error_code}: {e.message} (request {e.request_id})")
except ApiConnectionError as e:
    # The API could not be reached. For a charging request the outcome is unknown:
    # send it again later with the same key (e.idempotency_key).
    ...
```

#### When a registry call fails or times out

A charging request (registration, renewal, restore, transfer) can fail with a `ServerError`
whose `error_code` is `API_TIMEOUT` (504), `NETWORK_ERROR` or `REGISTRY_CONNECTION_FAILED` (502).
Your balance was not charged, but the registry may still have carried out the request. The SDK
does not resend these requests by itself, because sending a renewal again could renew the
domain twice. Check the domain first, for example `client.domains.get("example.com")` and its
expiry date after a renewal, then decide whether to send it again.

| Exception | When |
|---|---|
| `BadRequestError` | 400, 422 (`ValidationError` for `VALIDATION_FAILED` with `fields`, `IdempotencyKeyReusedError`) |
| `AuthenticationError` | 401: missing, wrong or revoked API key |
| `InsufficientBalanceError` | `BALANCE_INSUFFICIENT`, `BALANCE_NEGATIVE`, `TRANSFER_INSUFFICIENT_BALANCE` |
| `PermissionDeniedError` | 403 (`IpNotAllowedError` for `IP_NOT_ALLOWED`) |
| `NotFoundError` | 404 |
| `ConflictError` | 409 (`PriceChangedError`, `IdempotencyInProgressError`) |
| `RateLimitError` | 429, with `retry_after` |
| `ServerError` | 5xx except 501 |
| `ApiConnectionError` | no response (DNS, TLS, connection, timeout) |
| `UnexpectedResponseError` | a 2xx response without a JSON body |
| `InvalidArgumentError` | an argument the SDK refuses before sending (also a `ValueError`) |

All of them inherit from `LetoError`. `e.request_id` is the `X-Request-Id` of the response;
quote it when you contact LETO about a failed call.

### Pages

List methods return a `Page` (`AsyncPage` on the async client). Iterate it for the items of
that page, or call `auto_paginate()` for every item of every page (the next page is loaded only
when you get there).

```python
page = client.domains.list(query={"status": ["active", "expired"], "size": 100})
print(page.total, "domains")

for domain in page.auto_paginate():
    print(domain["domainName"], domain["expiresAt"]["date"])

# GET /transactions keeps its items in data.transactions; the Page handles that.
for tx in client.transactions.list(query={"type": "charge", "limit": 100}).auto_paginate():
    print(tx["createdAt"], tx["amount"], tx["currency"])
```

`page.items`, `page.body`, `page.pagination`, `page.total`, `page.has_next_page()`,
`page.next_page()` and `page.pages()` are there when you need them.

### Idempotency

Registration, renewal, restore and transfer charge your balance. The SDK sends an
`Idempotency-Key` with each of these calls (a new UUID unless you pass one), so it can retry them
after a lost connection without charging twice. A request with the same key within 24 hours
returns the first successful result.

When a retry might happen in another process (a queue worker, a crash, the next cron run),
create the key yourself and store it with your order before calling:

```python
from leto_domain_api import RequestOptions, generate_idempotency_key

order.idempotency_key = order.idempotency_key or generate_idempotency_key()   # save the order here
client.domains.renew("example.com", {"period": 1}, options=RequestOptions(idempotency_key=order.idempotency_key))

client.last_response.replayed   # True when the API returned the stored first result
```

Other endpoints do not accept a key; passing one raises `InvalidArgumentError`.

### Retries and timeouts

Up to 2 retries by default. The SDK waits as long as `Retry-After` says (up to `max_retry_after`
seconds) or backs off exponentially (about 0.5 s, 1 s, 2 s).

- GET requests: connection errors, 429, 502, 503 and 504.
- Requests with an Idempotency-Key: connection errors, 429, 503, 409
  `IDEMPOTENCY_REQUEST_IN_PROGRESS`, and 502 or 504 pages from a proxy in front of the API. A
  502 or 504 error from the API itself is not retried (see "When a registry call fails or times
  out").
- Other requests are never retried automatically.

```python
client = LetoClient(
    api_key,
    timeout=30,              # seconds per network operation, default 60 (connect at most 10)
    max_retries=3,           # default 2, 0 turns retries off
    max_retry_after=30,      # longest Retry-After to wait for, default 60
    user_agent_suffix="my-panel/2.1",
)

# Per call
client.domains.get("example.com", options=RequestOptions(max_retries=0))
```

`timeout` also accepts an `httpx.Timeout` when you want separate connect, read, write and pool
limits.

### Your own HTTP client

Pass an `httpx.Client` (an `httpx.AsyncClient` for `AsyncLetoClient`) to use your own proxies,
certificates, transport or event hooks. Its timeouts apply unless you also pass `timeout`. The
SDK does not close a client you passed in.

```python
import httpx

client = LetoClient(api_key, http_client=httpx.Client(proxy="http://proxy.internal:3128"))
```

### Type checking

The package ships type information (`py.typed`). Every method declares the `TypedDict` it takes
and returns (in `leto_domain_api.types`), so mypy, pyright and your editor know the keys:

```python
status = client.account.get_status()
status["balance"]["amout"]   # mypy: TypedDict "AccountBalanceDto" has no key "amout"
```

```python
from leto_domain_api.types import CreateDomainDto

body: CreateDomainDto = {"domain": "example.com", "period": 1, "contact": {...}}
```

Enum values are `Literal` types in requests and plain `str` in responses, because the API may
add values. At runtime the values are ordinary dicts; fields the API adds later pass through.

### Request details

```python
info = client.last_response
info.status_code       # 200
info.request_id        # X-Request-Id
info.idempotency_key   # key sent with the last request, if any
info.attempts          # 1, or more after retries
```

With concurrent calls on one `AsyncLetoClient`, `last_response` is whichever response arrived
last. Every `LetoApiError` carries its own `e.response`.

## Versioning

The SDK follows [semantic versioning](https://semver.org). The API itself only changes in
backward compatible ways; new endpoints and fields arrive in minor SDK releases.
`leto_domain_api.version.API` is the API document version the release was built from, and
`leto_domain_api.__version__` the version of the package.

## License

MIT. See [LICENSE](LICENSE).

---

## 한국어 안내

LETO 도메인 도매 API 의 Python SDK 입니다. Python 3.10 이상에서 동작하고, HTTP 클라이언트로
httpx 를 씁니다. 동기(`LetoClient`)와 asyncio(`AsyncLetoClient`) 클라이언트가 같은 메서드를
갖습니다.

```bash
pip install leto-domain-api
```

```python
from leto_domain_api import LetoClient

client = LetoClient(os.environ["LETO_API_KEY"])
status = client.account.get_status()               # 잔액·통화
page = client.domains.list(query={"size": 100})    # 목록은 Page, auto_paginate() 로 전체 순회
```

- API 키는 파트너 콘솔에서 발급하며 `Authorization: Bearer` 로 보냅니다. 서버에만 두세요.
- 오류는 `LetoApiError` 로 던집니다. 분기는 메시지가 아니라 `e.error_code` 로 합니다. 문의할
  때는 `e.request_id`(X-Request-Id)를 함께 알려 주세요.
- 등록·갱신·복원·이전 요청에는 `Idempotency-Key` 를 자동으로 붙이고, 연결 오류·429·503 에서
  같은 키로 다시 보냅니다. 다른 프로세스에서 다시 시도할 수 있다면 키를 직접 만들어 주문과 함께
  저장해 두세요(`generate_idempotency_key()`).
- 이 요청이 `API_TIMEOUT`(504)이나 `NETWORK_ERROR`·`REGISTRY_CONNECTION_FAILED`(502)로 실패하면
  잔액은 차감되지 않았지만 레지스트리에서는 처리됐을 수 있습니다. SDK 는 다시 보내지 않습니다
  (갱신이 두 번 될 수 있음). 도메인을 조회해(갱신이면 만료일) 확인한 뒤 다시 보낼지 정하세요.
- 가격이 바뀌는 사이에 결제되지 않게 하려면 견적의 `total` 을 `expectedPrice` 로 보내거나
  `register_with_quote()` 를 쓰세요. 가격이 달라졌으면 `PriceChangedError` 가 나고 차감되지
  않습니다.
- 전체 메서드 목록은 [OPERATIONS.md](OPERATIONS.md), API 문서는 https://partners.leto.kr/docs 에
  있습니다.
