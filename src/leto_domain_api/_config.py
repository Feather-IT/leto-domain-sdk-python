"""Validated client settings."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass

import httpx

from . import version
from ._errors import InvalidArgumentError

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
"""Timeouts of the built-in httpx client: 60 seconds, 10 to connect."""
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_RETRY_AFTER = 60.0

_API_KEY = re.compile(r"[\x21-\x7e]+")
_BASE_URL = re.compile(r"https?://[^/\s?#]+(/[^\s?#]*)?", re.IGNORECASE)
_PRINTABLE = re.compile(r"[\x20-\x7e]*")


@dataclass(frozen=True)
class Config:
    """Internal: settings shared by the sync and async transports."""

    api_key: str
    base_url: str
    max_retries: int
    max_retry_after: float
    user_agent: str
    timeout: httpx.Timeout | None
    """Timeout sent with every request, or None to leave it to the caller's own httpx client."""

    def __repr__(self) -> str:
        # Keeps the API key out of logs and debugger output.
        return (
            f"Config(api_key={masked_key(self.api_key)!r}, base_url={self.base_url!r}, "
            f"max_retries={self.max_retries}, max_retry_after={self.max_retry_after}, "
            f"user_agent={self.user_agent!r}, timeout={self.timeout!r})"
        )


def masked_key(api_key: str) -> str:
    """The first 12 characters of the key and an ellipsis."""
    return f"{api_key[:12]}..."


def build_config(
    api_key: str,
    *,
    base_url: str,
    timeout: float | httpx.Timeout | None,
    max_retries: int,
    max_retry_after: float,
    user_agent_suffix: str | None,
    own_client: bool,
) -> Config:
    """Checks the options of LetoClient and AsyncLetoClient."""
    if not isinstance(api_key, str):
        raise TypeError("api_key must be a string.")
    api_key = api_key.strip()
    if not _API_KEY.fullmatch(api_key):
        raise InvalidArgumentError(
            "The API key is empty or has invalid characters. Copy it from the partner console (leto_sk_...)."
        )

    if not isinstance(base_url, str) or not _BASE_URL.fullmatch(base_url):
        raise InvalidArgumentError(
            "base_url must be an http(s) URL without query string, for example https://domain-api.leto.kr."
        )
    base_url = base_url.rstrip("/")
    # The generated paths start with /api/v1, so a base URL that already ends with it is trimmed.
    if base_url.lower().endswith("/api/v1"):
        base_url = base_url[: -len("/api/v1")]

    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise InvalidArgumentError("max_retries must be an integer of 0 or more.")
    if not _positive_number(max_retry_after):
        raise InvalidArgumentError("max_retry_after must be a number of seconds greater than 0.")

    request_timeout: httpx.Timeout | None
    if timeout is None:
        request_timeout = DEFAULT_TIMEOUT if own_client else None
    elif isinstance(timeout, httpx.Timeout):
        request_timeout = timeout
    elif _positive_number(timeout):
        request_timeout = httpx.Timeout(float(timeout), connect=min(float(timeout), DEFAULT_TIMEOUT.connect or 10.0))
    else:
        raise InvalidArgumentError("timeout must be a number of seconds greater than 0 or an httpx.Timeout.")

    if user_agent_suffix is not None and (
        not isinstance(user_agent_suffix, str) or not _PRINTABLE.fullmatch(user_agent_suffix)
    ):
        raise InvalidArgumentError("user_agent_suffix must be a printable ASCII string.")
    user_agent = f"{version.USER_AGENT}/{version.SDK} python/{platform.python_version()}"
    if user_agent_suffix and user_agent_suffix.strip():
        user_agent += f" {user_agent_suffix.strip()}"

    return Config(
        api_key=api_key,
        base_url=base_url,
        max_retries=max_retries,
        max_retry_after=float(max_retry_after),
        user_agent=user_agent,
        timeout=request_timeout,
    )


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
