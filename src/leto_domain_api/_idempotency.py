"""Idempotency keys for the requests that charge the balance.

The SDK generates one per call when you do not pass one. Generate it yourself (or use your own
order ID) and store it with the order when a retry might happen in another process, for example
after a crash or a timeout: sending the same request with the same key within 24 hours returns
the first result instead of charging again.
"""

from __future__ import annotations

import re
import uuid

_KEY = re.compile(r"[\x21-\x7e]{1,255}")


def generate_idempotency_key() -> str:
    """A new random key (UUID version 4)."""
    return str(uuid.uuid4())


def is_valid_idempotency_key(key: str) -> bool:
    """True for a key the API accepts: 1 to 255 printable ASCII characters without spaces."""
    return isinstance(key, str) and _KEY.fullmatch(key) is not None
