"""Options for one call."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from ._errors import InvalidArgumentError
from ._idempotency import is_valid_idempotency_key

# Headers the SDK sets itself.
_RESERVED_HEADERS = frozenset({"authorization", "idempotency-key", "content-type", "content-length", "host"})
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HEADER_VALUE_FORBIDDEN = re.compile(r"[\r\n\0]")


@dataclass(frozen=True, kw_only=True)
class RequestOptions:
    """Options for one call, passed as the ``options`` keyword argument of any resource method.

    ```python
    client.domains.register(body, options=RequestOptions(idempotency_key=order.key))
    ```
    """

    idempotency_key: str | None = None
    """Idempotency-Key for a request that charges the balance. Only those requests accept one;
    the SDK generates a key when this is None."""
    max_retries: int | None = None
    """Retries for this call, instead of the client's ``max_retries``."""
    headers: Mapping[str, str] = field(default_factory=dict)
    """Extra request headers."""

    def __post_init__(self) -> None:
        if self.idempotency_key is not None and not is_valid_idempotency_key(self.idempotency_key):
            raise InvalidArgumentError("idempotency_key must be 1 to 255 printable ASCII characters without spaces.")
        if self.max_retries is not None and (
            not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool) or self.max_retries < 0
        ):
            raise InvalidArgumentError("max_retries must be an integer of 0 or more.")
        headers: dict[str, str] = {}
        for name, value in dict(self.headers).items():
            if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name):
                raise InvalidArgumentError(f'Invalid header name "{name}".')
            if name.lower() in _RESERVED_HEADERS:
                raise InvalidArgumentError(f"The SDK sets the {name} header itself.")
            if not isinstance(value, str) or _HEADER_VALUE_FORBIDDEN.search(value):
                raise InvalidArgumentError(f'Invalid value for header "{name}".')
            headers[name] = value
        # A copy, so changing the caller's dict later does not change these options.
        object.__setattr__(self, "headers", headers)
