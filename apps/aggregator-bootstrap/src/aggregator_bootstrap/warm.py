"""Capture and push a session — bootstrap, warm, and post-deploy hydrate.

Capturing from a logged-in `storage_state` and pushing is the same for a first
login and a periodic warm. The difference is only how the state got there: a
headed `login` writes it; a deploy/restart hydrates it from the API; a warm
reopens it, rotates the anti-bot cookie, and pushes the refresh back.

Keeta is the exception: it has no httpx sweep, so warming it also pulls its
orders in-page (its `mtgsig` signing lives in the page) and pushes the raw
payloads to `/aggregators/keeta/orders`.
"""

from __future__ import annotations

import logging
from typing import Any

from .browser import NeedsHumanLogin, NotLoggedInError, _storage_state_path
from .config import settings
from .hydrate import hydrate_from_api
from .keeta_pull import fetch_keeta_orders
from .push import push_keeta_orders, push_session
from .session_capture import capture, payload_from_probe

logger = logging.getLogger(__name__)


async def hydrate_then_warm(channel: str | None = None) -> dict[str, Any]:
    """Pull sessions from the API, then warm one channel or all of them.

    Called on every worker start so a new image with an empty volume resumes
    the previous session instead of looking logged-out.
    """
    try:
        restored = await hydrate_from_api()
        logger.info("hydrated channels from API: %s", restored or "(none)")
    except Exception:  # noqa: BLE001 — local files may still be good
        logger.exception(
            "hydrate from API failed; continuing with any local storage_state"
        )
    if channel:
        return await warm_channel(channel)
    return {"hydrated": True}


async def warm_channel(channel: str) -> dict[str, Any]:
    """Re-capture the channel's session from its stored state and push it.

    Keeta is the exception: it has no httpx replay path, so warming it means
    pulling its orders in-page (mtgsig is signed there) and pushing those, rather
    than capturing a session fingerprint for the sweep to replay.
    """
    if channel == "keeta":
        # Still recapture the browser state so a Keeta warm refreshes cookies
        # in the DB, then pull orders in-page.
        try:
            payload = await capture("keeta")
            await push_session(payload)
        except NeedsHumanLogin:
            logger.error("keeta needs a headed login; skipping in-page pull")
            raise
        return await pull_keeta_orders_in_page()
    payload = await capture(channel)
    result = await push_session(payload)
    logger.info("pushed %s session: %s", channel, result.get("status"))
    return result


async def push_probe(channel: str, result) -> dict[str, Any]:
    """Push a session assembled from an already-open probe (headed login)."""
    payload = payload_from_probe(channel, result)
    pushed = await push_session(payload)
    logger.info("pushed %s session after login: %s", channel, pushed.get("status"))
    return pushed


async def pull_keeta_orders_in_page(*, months_back: int = 1) -> dict[str, Any]:
    """Fetch Keeta's getOrders in-page (mtgsig-signed) and push the payloads.

    Uses the hydrated Playwright `storage_state` + sessionStorage, *not* the
    headed Chrome profile. That profile often keeps a stale HK login redirect
    that clears `LOGIN_ACCOUNTID` even when the API session blob is good.
    Playwright is imported lazily so this module imports without the browser lib.
    """
    from .browser import _open_storage_state_context
    from .engine import async_playwright

    state = _storage_state_path("keeta")
    if not state.exists():
        raise NotLoggedInError(
            f"keeta session state missing at {state}; run a login/bootstrap first"
        )

    async with async_playwright() as pw:
        opened = await _open_storage_state_context(pw, "keeta")
        try:
            payloads = await fetch_keeta_orders(opened.context, months_back=months_back)
        finally:
            await opened.close()

    if not payloads:
        logger.warning("keeta: no getOrders payloads fetched; nothing to push")
        return {"ingested": 0, "payloads": 0}

    result = await push_keeta_orders(payloads)
    logger.info("pushed %d keeta getOrders payloads: %s", len(payloads), result)
    return result


__all__ = [
    "warm_channel",
    "pull_keeta_orders_in_page",
    "push_keeta_orders",
    "hydrate_then_warm",
    "push_probe",
]
