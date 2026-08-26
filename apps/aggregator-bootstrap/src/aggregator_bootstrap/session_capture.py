"""Assemble the push payload from a probed request — the pure, testable core.

Given the cookies and the headers of one real authenticated request, this maps
them into the exact `{cookies, tokens, header_profile}` shape the mm-ecommerce
providers replay. No Playwright here, so it is unit-tested directly.
"""

from __future__ import annotations

from typing import Any

from .channels.probes import CHANNEL_PROBES


def build_session(
    channel: str, cookies: list[dict], request_headers: dict[str, str]
) -> dict[str, Any]:
    """Map raw cookies + one request's headers to the API's session shape."""
    probe = CHANNEL_PROBES[channel]
    # Headers arrive lower-cased from Playwright; keep them as sent.
    lower = {k.lower(): v for k, v in request_headers.items()}

    header_profile = {k: lower[k] for k in probe.header_keys if k in lower and lower[k]}

    tokens: dict[str, Any] = {}
    for header_name, token_key in probe.token_from_header.items():
        value = lower.get(header_name)
        if value:
            tokens[token_key] = value

    cookie_map = {c["name"]: c["value"] for c in cookies if c.get("name")}
    for cookie_name, token_key in probe.token_from_cookie.items():
        if cookie_name in cookie_map:
            tokens[token_key] = cookie_map[cookie_name]

    return {
        "channel": channel,
        "cookies": cookie_map,
        "tokens": tokens,
        "header_profile": header_profile,
    }


async def capture(channel: str) -> dict[str, Any]:
    """Probe the channel in a browser and build its session payload."""
    from .browser import probe_channel  # lazy — pulls Playwright

    cookies, headers, _ = await probe_channel(channel)
    return build_session(channel, cookies, headers)
