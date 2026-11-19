"""Python client for the LETO Domain API.

```python
from leto_domain_api import LetoClient

client = LetoClient("leto_sk_...")
print(client.account.get_status()["balance"])
```

See README.md for a guide and OPERATIONS.md for every method.
"""

from . import types, version
from ._client import AsyncLetoClient, LetoClient
from ._errors import (
    ApiConnectionError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyReusedError,
    InsufficientBalanceError,
    InvalidArgumentError,
    IpNotAllowedError,
    LetoApiError,
    LetoError,
    NotFoundError,
    PermissionDeniedError,
    PriceChangedError,
    QuoteDeclinedError,
    RateLimitError,
    ServerError,
    UnexpectedResponseError,
    ValidationError,
)
from ._idempotency import generate_idempotency_key, is_valid_idempotency_key
from ._options import RequestOptions
from ._page import AsyncPage, Page, PaginationInfo
from ._response import ResponseInfo

__version__ = version.SDK

__all__ = [
    "ApiConnectionError",
    "AsyncLetoClient",
    "AsyncPage",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "IdempotencyInProgressError",
    "IdempotencyKeyReusedError",
    "InsufficientBalanceError",
    "InvalidArgumentError",
    "IpNotAllowedError",
    "LetoApiError",
    "LetoClient",
    "LetoError",
    "NotFoundError",
    "Page",
    "PaginationInfo",
    "PermissionDeniedError",
    "PriceChangedError",
    "QuoteDeclinedError",
    "RateLimitError",
    "RequestOptions",
    "ResponseInfo",
    "ServerError",
    "UnexpectedResponseError",
    "ValidationError",
    "__version__",
    "generate_idempotency_key",
    "is_valid_idempotency_key",
    "types",
    "version",
]
