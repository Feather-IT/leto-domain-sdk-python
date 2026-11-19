"""Read-only calls against the real API with a sandbox key.

Skipped unless LETO_SDK_SANDBOX_KEY is set, and left out of the normal test run (marker
``smoke``):

    LETO_SDK_SANDBOX_KEY=leto_sk_... make python-smoke

LETO_SDK_BASE_URL overrides the base URL. Nothing here charges, creates or changes anything.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from leto_domain_api import LetoClient

pytestmark = pytest.mark.smoke


@pytest.fixture
def sandbox() -> Iterator[LetoClient]:
    key = os.environ.get("LETO_SDK_SANDBOX_KEY", "")
    if key == "":
        pytest.skip("Set LETO_SDK_SANDBOX_KEY to run the sandbox smoke test.")
    base_url = os.environ.get("LETO_SDK_BASE_URL", "") or "https://domain-api.leto.kr"
    with LetoClient(key, base_url=base_url, user_agent_suffix="leto-sdk-smoke-test") as client:
        yield client


def test_account_status(sandbox: LetoClient) -> None:
    status = sandbox.account.get_status()

    assert status["isSandbox"], "LETO_SDK_SANDBOX_KEY must belong to a sandbox account."
    assert isinstance(status["balance"]["currency"], str)
    assert sandbox.last_response is not None
    assert sandbox.last_response.request_id is not None


def test_tld_list(sandbox: LetoClient) -> None:
    page = sandbox.pricing.list_tlds(query={"size": 5})

    assert len(page) <= 5
    assert (page.total or 0) > 0
    for tld in page:
        assert isinstance(tld["tld"], str)
