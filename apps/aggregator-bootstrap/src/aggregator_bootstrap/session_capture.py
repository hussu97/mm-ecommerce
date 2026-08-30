"""Assemble the push payload from a probed request — the pure, testable core.

Given the cookies and the headers of one real authenticated request, this maps
them into the `{cookies, tokens, header_profile, storage_state}` shape the
mm-ecommerce API stores. `storage_state` is the Playwright blob plus
origin-scoped sessionStorage: that is what a restarted worker hydrates so a
deploy does not force a re-login. No Playwright here, so it is unit-tested
directly.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .channels.probes import CHANNEL_PROBES

#: Cookie / localStorage / sessionStorage keys that are credentials, not noise.
_TOKEN_KEY = re.compile(
    r"(token|auth|jwt|refresh|access|bearer|session|sid|id_token)", re.I
)

#: Skip values that are clearly not a bearer (huge JSON blobs, empty).
_MAX_TOKEN_CHARS = 8_192

#: Cut a JWT's life short by this much, same idea as GrubOps: a call that
#: begins inside the margin still has the whole margin to finish in.
_EXPIRY_SKEW = timedelta(seconds=120)

_ANTI_BOT_COOKIES = ("_px3", "bm_sv", "_abck", "ak_bmsc", "_pxvid", "WEBDFPID")

#: Cookies whose expiry must NEVER gate a session's liveness. Web-analytics
#: cookies (Google's `_ga*`/`_gid`/`_gat`) and Cloudflare's `__cf_bm` bot cookie
#: are not credentials: `_gat` lives ONE MINUTE and `__cf_bm` ~30 (and the edge
#: re-issues `__cf_bm` on every request, so curl_cffi always sends a fresh one).
#: Careem carries a `_gat`, so taking `min` across ALL cookies made its session
#: look expired ~30s after every capture — the ingest then skipped it as "not
#: live" and burned 360s waiting for a needless reauth, though its SESSION cookie
#: (~35h) and bearer (~72h) were both fine. Drop this rotating junk before taking
#: the min so the gate reflects the real session cookie. (Only matters for a
#: channel with no `_ANTI_BOT_COOKIES` — talabat/noon already gate on those.)
_ROTATING_COOKIE_PREFIXES = ("_ga", "_gid", "_gat")


def _gates_liveness(cookie_name: str) -> bool:
    """Whether a cookie's expiry should count toward the session's liveness."""
    if cookie_name == "__cf_bm":
        return False
    return not cookie_name.startswith(_ROTATING_COOKIE_PREFIXES)


def _looks_like_token_key(name: str) -> bool:
    return bool(name) and bool(_TOKEN_KEY.search(name))


def jwt_expiry(token: str) -> datetime | None:
    """The `exp` claim of an unverified JWT, or None if it is not a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError, TypeError):
        return None
    exp = data.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


def harvest_tokens_from_map(mapping: dict[str, Any]) -> dict[str, str]:
    """Lift credential-shaped keys out of a cookie/storage map."""
    tokens: dict[str, str] = {}
    for key, value in mapping.items():
        if not _looks_like_token_key(str(key)):
            continue
        if not isinstance(value, str) or not value or len(value) > _MAX_TOKEN_CHARS:
            continue
        tokens[str(key)] = value
    return tokens


def harvest_storage_tokens(playwright_state: dict[str, Any]) -> dict[str, str]:
    """Lift token-like keys out of Playwright localStorage origins."""
    tokens: dict[str, str] = {}
    for origin in playwright_state.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        for item in origin.get("localStorage") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                tokens.update(harvest_tokens_from_map({name: value}))
    return tokens


def earliest_token_expiry(tokens: dict[str, str]) -> datetime | None:
    """The soonest JWT `exp` among harvested tokens, minus skew."""
    expiries = [jwt_expiry(value) for value in tokens.values()]
    known = [exp for exp in expiries if exp is not None]
    if not known:
        return None
    return min(known) - _EXPIRY_SKEW


def cookie_expiry_from_playwright(
    playwright_state: dict[str, Any],
) -> datetime | None:
    """The soonest anti-bot / session cookie expiry Playwright recorded.

    Playwright uses unix seconds; `-1` means a session cookie (no persistent
    expiry) and is skipped. Prefer anti-bot cookies when present — those are
    the ones the warmer has to rotate.
    """
    cookies = playwright_state.get("cookies") or []
    timestamps: list[float] = []
    antibot: list[float] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        expires = cookie.get("expires")
        if not isinstance(expires, (int, float)) or expires <= 0:
            continue
        name = cookie.get("name") or ""
        if not _gates_liveness(name):
            continue  # rotating analytics / Cloudflare bot cookie — not a credential
        timestamps.append(float(expires))
        if name in _ANTI_BOT_COOKIES:
            antibot.append(float(expires))
    chosen = antibot or timestamps
    if not chosen:
        return None
    return datetime.fromtimestamp(min(chosen), tz=UTC)


def bundle_browser_state(
    playwright_state: dict[str, Any],
    *,
    session_storage: dict[str, str] | None = None,
    origin: str = "",
    extra_session_storage: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """The blob stored encrypted on `aggregator_session.storage_state`.

    `playwright` is what `browser.new_context(storage_state=...)` accepts.
    `session_storage` is origin-scoped because Playwright's storage_state
    does not persist sessionStorage (Keeta keeps shop ids there).
    """
    by_origin: dict[str, dict[str, str]] = {}
    if extra_session_storage:
        by_origin.update(extra_session_storage)
    if origin and session_storage:
        by_origin[origin] = dict(session_storage)
    return {
        "playwright": playwright_state,
        "session_storage": by_origin,
        "captured_at": datetime.now(UTC).isoformat(),
    }


def split_browser_state(
    blob: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Unpack a stored blob into (playwright storage_state, sessionStorage).

    A raw Playwright `storage_state` (cookies+origins, no wrapper) is accepted
    so an older row still hydrates.
    """
    if not blob:
        return {}, {}
    if "playwright" in blob and isinstance(blob["playwright"], dict):
        extra = blob.get("session_storage") or {}
        if not isinstance(extra, dict):
            extra = {}
        return blob["playwright"], {
            str(origin): dict(items)
            for origin, items in extra.items()
            if isinstance(items, dict)
        }
    return blob, {}


def build_session(
    channel: str,
    cookies: list[dict],
    request_headers: dict[str, str],
    *,
    playwright_state: dict[str, Any] | None = None,
    session_storage: dict[str, str] | None = None,
    origin: str = "",
    extra_session_storage: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Map raw cookies + one request's headers to the API's session shape."""
    probe = CHANNEL_PROBES[channel]
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

    tokens.update(harvest_tokens_from_map(cookie_map))
    if playwright_state:
        tokens.update(harvest_storage_tokens(playwright_state))
    if session_storage:
        tokens.update(harvest_tokens_from_map(session_storage))

    state = playwright_state or {"cookies": cookies, "origins": []}
    bundled = bundle_browser_state(
        state,
        session_storage=session_storage,
        origin=origin,
        extra_session_storage=extra_session_storage,
    )

    token_exp = earliest_token_expiry(
        {k: v for k, v in tokens.items() if isinstance(v, str)}
    )
    cookie_exp = cookie_expiry_from_playwright(state)
    return {
        "channel": channel,
        "cookies": cookie_map,
        "tokens": tokens,
        "header_profile": header_profile,
        "storage_state": bundled,
        "token_expires_at": token_exp.isoformat() if token_exp else None,
        "cookie_expires_at": cookie_exp.isoformat() if cookie_exp else None,
    }


async def capture(channel: str) -> dict[str, Any]:
    """Probe the channel in a browser and build its session payload."""
    from .browser import load_extra_state, probe_channel  # lazy — pulls Playwright

    result = await probe_channel(channel)
    return build_session(
        channel,
        result.cookies,
        result.request_headers,
        playwright_state=result.playwright_state,
        session_storage=result.session_storage,
        origin=result.origin,
        extra_session_storage=load_extra_state(channel),
    )


def payload_from_probe(channel: str, result: Any) -> dict[str, Any]:
    """Build a push payload from an already-probed (or interactively logged-in) result."""
    from .browser import load_extra_state

    return build_session(
        channel,
        result.cookies,
        result.request_headers,
        playwright_state=result.playwright_state,
        session_storage=result.session_storage,
        origin=result.origin,
        extra_session_storage=load_extra_state(channel),
    )
