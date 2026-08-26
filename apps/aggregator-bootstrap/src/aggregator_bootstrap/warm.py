"""Capture and push a session — the operation both the bootstrap and warm run.

Capturing from a logged-in `storage_state` and pushing is the same for a first
bootstrap and a periodic warm; the difference is only whether a login had to
happen first (a full bootstrap does the OTP login to establish the state; a warm
assumes it exists and just re-runs the sensor by loading a page). Both end here.

Keeta is the exception: it has no httpx sweep, so warming it also pulls its
orders in-page and pushes them. That in-page pull is the one piece still to be
ported from the standalone scraper's `channels/keeta` fetch (it needs the page's
own signing), and is stubbed here with a clear error until it lands.
"""

from __future__ import annotations

import logging
from typing import Any

from .browser import NotLoggedInError, _storage_state_path
from .config import settings
from .keeta_pull import fetch_keeta_orders
from .push import push_keeta_orders, push_session
from .session_capture import capture

logger = logging.getLogger(__name__)


async def warm_channel(channel: str) -> dict[str, Any]:
    """Re-capture the channel's session from its stored state and push it.

    Keeta is the exception: it has no httpx replay path, so warming it means
    pulling its orders in-page (mtgsig is signed there) and pushing those, rather
    than capturing a session fingerprint for the sweep to replay.
    """
    if channel == "keeta":
        return await pull_keeta_orders_in_page()
    payload = await capture(channel)
    result = await push_session(payload)
    logger.info("pushed %s session: %s", channel, result.get("status"))
    return result


async def pull_keeta_orders_in_page(*, months_back: int = 1) -> dict[str, Any]:
    """Fetch Keeta's getOrders in-page (mtgsig-signed) and push the payloads.

    Opens a browser context from the stored Keeta storage_state, evaluates the
    signed `getOrders` fetch in the page (`keeta_pull.fetch_keeta_orders`), and
    hands the raw payloads to the API, which parses each with `keeta_provider`.
    Playwright is imported lazily, so this module imports without the browser lib.
    """
    from playwright.async_api import async_playwright  # lazy

    state = _storage_state_path("keeta")
    if not state.exists():
        raise NotLoggedInError(
            f"keeta session state missing at {state}; run a login/bootstrap first"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.HEADLESS)
        context = await browser.new_context(
            storage_state=str(state), accept_downloads=True
        )
        try:
            payloads = await fetch_keeta_orders(context, months_back=months_back)
        finally:
            await browser.close()

    if not payloads:
        logger.warning("keeta: no getOrders payloads fetched; nothing to push")
        return {"ingested": 0, "payloads": 0}

    result = await push_keeta_orders(payloads)
    logger.info("pushed %d keeta getOrders payloads: %s", len(payloads), result)
    return result


__all__ = ["warm_channel", "pull_keeta_orders_in_page", "push_keeta_orders"]
