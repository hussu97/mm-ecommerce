"""The Playwright side: open a logged-in context and read one real request.

Playwright is imported lazily, inside the functions that use it, so the rest of
the package (and its tests) import without the browser library installed — the
worker only needs it at run time, in its own container.

The model is "capture from a logged-in session": a persisted `storage_state`
(established once, by a human or the OTP login flow) is reopened each run, the
channel's probe page is loaded, and the first authenticated API call it makes is
intercepted so its headers become the fingerprint. If the probe lands on a login
page the session is stale and `NotLoggedInError` is raised — re-login (OTP) is a
separate, heavier step.
"""

from __future__ import annotations

import os
from pathlib import Path

from .channels.probes import CHANNEL_PROBES, ChannelProbe
from .config import settings


class NotLoggedInError(RuntimeError):
    """The stored session no longer authenticates — a full re-login is needed."""


def _storage_state_path(channel: str) -> Path:
    return Path(settings.STORAGE_STATE_DIR) / f"{channel}.session.json"


async def probe_channel(channel: str) -> tuple[list[dict], dict[str, str], str]:
    """Load the channel's probe page and return (cookies, request_headers, url).

    `cookies` is Playwright's cookie list; `request_headers` are the headers of
    the first API call matching the probe's `match`; `url` is where the page
    ended (used to detect a login redirect).
    """
    from playwright.async_api import async_playwright  # lazy

    probe: ChannelProbe = CHANNEL_PROBES[channel]
    state = _storage_state_path(channel)
    captured: dict[str, str] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.HEADLESS)
        context = await browser.new_context(
            storage_state=str(state) if state.exists() else None,
            accept_downloads=True,
        )
        page = await context.new_page()

        async def _on_request(request) -> None:
            if not captured and probe.match in request.url:
                captured.update(request.headers)

        page.on("request", _on_request)
        await page.goto(probe.probe_url, timeout=settings.PROBE_TIMEOUT_MS)
        # Give the SPA a moment to fire its data calls.
        await page.wait_for_timeout(5000)

        final_url = page.url
        cookies = await context.cookies()
        # Persist the (possibly refreshed) state so the next warm resumes it.
        os.makedirs(settings.STORAGE_STATE_DIR, exist_ok=True)
        await context.storage_state(path=str(state))
        await browser.close()

    if any(w in final_url.lower() for w in ("login", "signin", "identity", "auth")):
        raise NotLoggedInError(f"{channel} session is stale (landed on {final_url})")
    return cookies, captured, final_url
