"""Reading the Retry-After header."""

from __future__ import annotations

import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

_SECONDS = re.compile(r"\d+(\.\d+)?")


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds to wait, from either form of the header (seconds or an HTTP date).

    Returns None when the header is absent or unreadable. A date in the past gives 0.
    """
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if _SECONDS.fullmatch(value):
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if moment.tzinfo is None:
        # HTTP dates are always GMT.
        moment = moment.replace(tzinfo=timezone.utc)
    return float(max(0.0, moment.timestamp() - (time.time() if now is None else now)))
