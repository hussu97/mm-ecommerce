"""Hydrate local Playwright state from the API — the deploy/restart path.

The encrypted `aggregator_session` row is the source of truth. A new container
with an empty `/data` volume (a deploy, a crash, a new VM image) calls this
before it opens a browser, writes `{channel}.session.json` + extra
sessionStorage, and only then warms. Without this, every restart looked like a
logged-out session and fell into a re-login.
"""

from __future__ import annotations

import logging
from typing import Any

from .browser import persist_extra_state, persist_playwright_state
from .channels.probes import CHANNEL_PROBES
from .push import pull_sessions
from .session_capture import split_browser_state

logger = logging.getLogger(__name__)


def apply_bundle(bundle: dict[str, Any]) -> bool:
    """Write one API bundle to local files. Returns whether a Playwright state landed."""
    channel = bundle.get("channel")
    if channel not in CHANNEL_PROBES:
        logger.warning("hydrate: skipping unknown channel %s", channel)
        return False
    blob = bundle.get("storage_state")
    if not isinstance(blob, dict) or not blob:
        logger.info(
            "hydrate: %s has cookies/tokens in the DB but no storage_state; "
            "httpx ingest can still replay, the browser needs a headed login",
            channel,
        )
        return False
    playwright, extra = split_browser_state(blob)
    if not playwright:
        return False
    persist_playwright_state(channel, playwright)
    if extra:
        persist_extra_state(channel, extra)
    logger.info("hydrate: wrote %s storage_state from the API", channel)
    return True


async def hydrate_from_api() -> list[str]:
    """Pull every session the API holds and write local state files.

    Returns the channels that got a Playwright state. Network/auth failures
    propagate — the caller decides whether to fall back to whatever is already
    on disk.
    """
    bundles = await pull_sessions()
    restored: list[str] = []
    for bundle in bundles:
        if apply_bundle(bundle):
            restored.append(str(bundle["channel"]))
    return restored
