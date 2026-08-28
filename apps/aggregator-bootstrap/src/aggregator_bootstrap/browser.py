"""The browser side: real Chrome for login, Playwright only to harvest.

Playwright is imported lazily so unit tests import this module
without a browser installed.

The model is "one human login, then hydrate forever":

- `login_interactive` starts **Google Chrome as an OS process** (dedicated
  profile, remote-debugging port, no Playwright attached). Cloudflare therefore
  sees a normal headed Chrome. We only `connect_over_cdp` after a session
  cookie appears (or the operator is clearly past the challenge), then persist
  `storage_state` + sessionStorage.
- `probe_channel` reopens that profile, loads the probe page, and intercepts
  one authenticated request so its headers become the fingerprint the httpx
  ingest replays.
- If the probe lands on a login page the session is fully dead —
  `NeedsHumanLogin`. The worker does not drive IMAP OTP.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .channels.login import (
    LOGIN_START_URLS,
    deliveroo_login_form_visible,
    fill_deliveroo_login,
    login_careem,
    login_noon,
    login_talabat,
    page_looks_authenticated,
)
from .channels.probes import CHANNEL_PROBES, ChannelProbe
from .config import settings
from .engine import async_playwright, evaluate_in_page
from .fingerprint import (
    CONTAINER_CHROME_ARGS,
    chrome_binary,
    chrome_profile_dir,
    context_kwargs,
    free_debug_port,
    standalone_chrome_args,
    warm_persistent_kwargs,
)

logger = logging.getLogger(__name__)


class NotLoggedInError(RuntimeError):
    """The stored session no longer authenticates — a human login is needed."""


class NeedsHumanLogin(NotLoggedInError):
    """The session cannot be saved from here; run `login --channel` headed."""


#: How long the headed login waits for the operator (OTP, captcha, passkey).
_LOGIN_WAIT_SECONDS = 45 * 60

#: Let Cloudflare's JS challenge run with no CDP client attached.
_UNATTACHED_SECONDS = 12

#: Cookie names that mean "this channel's portal session exists".
_SESSION_COOKIE_NAMES: dict[str, tuple[str, ...]] = {
    "deliveroo": ("token",),
    "talabat": ("accessToken", "refreshToken"),
    "noon": ("access_token", "token", "noon_customer_token"),
    "keeta": ("token", "access_token", "SESSION"),
    "careem": ("token", "access_token", "sid", "authorization"),
}


@dataclass
class ProbeResult:
    """Everything one probe (or headed login) lifts off a live context."""

    cookies: list[dict]
    request_headers: dict[str, str]
    final_url: str
    playwright_state: dict[str, Any]
    session_storage: dict[str, str] = field(default_factory=dict)
    origin: str = ""


@dataclass
class _Opened:
    context: Any
    browser: Any
    persistent: bool
    chrome: Any = None  # subprocess.Popen when we spawned Chrome ourselves

    async def close(self) -> None:
        if self.persistent and self.chrome is None:
            await self.context.close()
        elif self.browser is not None and self.chrome is None:
            await self.browser.close()
        _stop_chrome(self.chrome)


def _bearer_aud(auth_header: str) -> str | None:
    """The `aud` claim of a `Bearer <jwt>` header, or None. Used to tell Careem's
    OIDC identity token (aud=com.careem.internal) from its partner-API token."""
    if not auth_header.lower().startswith("bearer "):
        return None
    parts = auth_header[7:].split(".")
    if len(parts) < 2:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(pad)).get("aud")
    except Exception:  # noqa: BLE001 — a non-JWT bearer just has no aud
        return None


def _storage_state_path(channel: str) -> Path:
    return Path(settings.STORAGE_STATE_DIR) / f"{channel}.session.json"


def _extra_state_path(channel: str) -> Path:
    """Origin-scoped sessionStorage the Playwright `storage_state` does not keep."""
    return Path(settings.STORAGE_STATE_DIR) / f"{channel}.extra.json"


def persist_playwright_state(channel: str, playwright_state: dict[str, Any]) -> Path:
    """Write a Playwright `storage_state` file the next context can reopen."""
    path = _storage_state_path(channel)
    os.makedirs(path.parent, exist_ok=True)
    path.write_text(json.dumps(playwright_state), encoding="utf-8")
    return path


def persist_extra_state(
    channel: str, session_storage_by_origin: dict[str, dict[str, str]]
) -> Path:
    path = _extra_state_path(channel)
    os.makedirs(path.parent, exist_ok=True)
    path.write_text(json.dumps(session_storage_by_origin), encoding="utf-8")
    return path


def load_extra_state(channel: str) -> dict[str, dict[str, str]]:
    path = _extra_state_path(channel)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        origin: dict(items)
        for origin, items in loaded.items()
        if isinstance(items, dict)
    }


def _session_storage_init_script(by_origin: dict[str, dict[str, str]]) -> str:
    payload = json.dumps(by_origin)
    return f"""
() => {{
  const byOrigin = {payload};
  const items = byOrigin[location.origin];
  if (!items) return;
  for (const [key, value] of Object.entries(items)) {{
    try {{ sessionStorage.setItem(key, value); }} catch (e) {{}}
  }}
}}
"""


async def _collect_session_storage(page) -> dict[str, str]:
    try:
        return await evaluate_in_page(
            page,
            """() => {
              const out = {};
              for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key) out[key] = sessionStorage.getItem(key);
              }
              return out;
            }""",
        )
    except Exception:  # noqa: BLE001 — origin may not allow storage
        return {}


def _origin_of(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _page_is_closed(page) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:  # noqa: BLE001
        return True


def chrome_cookie_names(profile: Path) -> set[str]:
    """Cookie *names* in a live Chrome profile (values are often encrypted)."""
    candidates = [
        profile / "Default" / "Network" / "Cookies",
        profile / "Default" / "Cookies",
    ]
    src = next((path for path in candidates if path.exists()), None)
    if src is None:
        return set()
    tmp = Path(tempfile.mkdtemp(prefix="agg-cookies-"))
    try:
        dst = tmp / "Cookies"
        shutil.copy2(src, dst)
        for suffix in ("-wal", "-shm"):
            extra = Path(str(src) + suffix)
            if extra.exists():
                shutil.copy2(extra, Path(str(dst) + suffix))
        con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT name FROM cookies").fetchall()
        finally:
            con.close()
        return {str(row[0]) for row in rows if row and row[0]}
    except Exception:  # noqa: BLE001 — WAL copy can be inconsistent
        return set()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _session_cookies_present(channel: str, names: set[str]) -> bool:
    wanted = _SESSION_COOKIE_NAMES.get(channel, ())
    return any(name in names for name in wanted)


def _stop_chrome(proc: Any) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        proc.kill()


async def _wait_for_cdp(port: int, *, timeout_s: float = 30) -> None:
    import httpx

    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/json/version"
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url, timeout=1.0)
                if response.status_code == 200:
                    return
            except Exception:  # noqa: BLE001 — Chrome is still booting
                pass
            await asyncio.sleep(0.25)
    raise NeedsHumanLogin(f"Chrome did not open a debug port on {port}")


def _spawn_chrome(*, profile: Path, port: int, url: str) -> Any:
    import subprocess

    binary = chrome_binary()
    if not binary:
        raise NeedsHumanLogin(
            "Google Chrome is not installed. Install it from "
            "https://www.google.com/chrome/ then re-run login."
        )
    os.makedirs(profile, exist_ok=True)
    args = standalone_chrome_args(
        binary=binary, user_data_dir=profile, port=port, url=url
    )
    logger.info("spawning Chrome profile=%s port=%s", profile, port)
    return subprocess.Popen(  # noqa: S603 — args are ours, not user input
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def _connect_cdp(pw, port: int):
    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    if not browser.contexts:
        raise NeedsHumanLogin("Chrome opened but has no browser context")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else await context.new_page()
    return browser, context, page


async def _snapshot_context(
    channel: str, context, page, captured: dict[str, str]
) -> ProbeResult:
    final_url = page.url
    cookies = await context.cookies()
    os.makedirs(settings.STORAGE_STATE_DIR, exist_ok=True)
    state_path = _storage_state_path(channel)
    await context.storage_state(path=str(state_path))
    playwright_state = json.loads(state_path.read_text(encoding="utf-8"))
    session_storage = await _collect_session_storage(page)
    origin = _origin_of(final_url)
    if origin and session_storage:
        extra = load_extra_state(channel)
        extra[origin] = session_storage
        persist_extra_state(channel, extra)
    return ProbeResult(
        cookies=cookies,
        request_headers=dict(captured),
        final_url=final_url,
        playwright_state=playwright_state,
        session_storage=session_storage,
        origin=origin,
    )


#: Extra chromium launch flags per channel. noon's console sits behind an
#: Akamai edge that trips Playwright/Chromium's HTTP/2 client — every
#: `page.goto` to `restaurant.noon.partners` fails with
#: net::ERR_HTTP2_PROTOCOL_ERROR, which aborted the warm so noon's bm_sv/_abck
#: was never rotated. Forcing HTTP/1.1 for noon's browser gets the page to load
#: (the httpx ingest, on curl_cffi, negotiates HTTP/2 fine — this flag is only
#: for the Playwright warm). Real Chrome in `login_interactive` is unaffected and
#: keeps HTTP/2. Other channels keep their natural HTTP/2 fingerprint.
_CHANNEL_LAUNCH_ARGS: dict[str, list[str]] = {
    "noon": ["--disable-http2"],
}


def _clear_stale_singleton_locks(user_data_dir: Path) -> None:
    """Remove a previous Chrome's Singleton* lock files from the profile.

    Chrome writes `SingletonLock` (a symlink to `<host>-<pid>`) into the
    user-data dir and refuses to launch when it points at a process it cannot
    verify is dead — "the profile appears to be in use by another Chrome process
    on another computer". A warm that was SIGKILLed (a timeout, an OOM) leaves
    that lock behind, and since each warm runs in a fresh container the hostname
    never matches, so every subsequent warm is blocked forever. These files are
    pure runtime state (no session data), safe to delete before we relaunch.
    """
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (user_data_dir / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover — best effort
            logger.warning("could not clear %s in %s: %s", name, user_data_dir, exc)


async def _launch_persistent(pw, user_data_dir: Path, *, headed: bool, channel: str):
    os.makedirs(user_data_dir, exist_ok=True)
    _clear_stale_singleton_locks(user_data_dir)
    kwargs = warm_persistent_kwargs(headed=headed)
    extra_args = _CHANNEL_LAUNCH_ARGS.get(channel)
    if extra_args:
        kwargs["args"] = [*kwargs.get("args", []), *extra_args]
    try:
        return await pw.chromium.launch_persistent_context(str(user_data_dir), **kwargs)
    except Exception as exc:
        if kwargs.get("channel") != "chrome":
            raise
        # Fall back to Playwright's bundled Chromium — present on a dev laptop
        # (`playwright install`) but NOT in the container image, which ships only
        # branded Chrome. So in the container this fallback cannot succeed; if it
        # fails, re-raise the ORIGINAL branded-Chrome error (e.g. a profile lock),
        # not the confusing "Executable doesn't exist …/chromium-1234" that masks
        # the real cause.
        logger.warning("branded Chrome failed (%s); trying bundled Chromium", exc)
        fallback = dict(kwargs)
        fallback.pop("channel", None)
        try:
            return await pw.chromium.launch_persistent_context(
                str(user_data_dir), **fallback
            )
        except Exception:
            raise exc from None


async def _open_context(pw, channel: str, *, headed: bool) -> _Opened:
    """Open the channel's Chrome profile for a warm/probe (already logged in)."""
    extra = load_extra_state(channel)
    profile = chrome_profile_dir(settings.STORAGE_STATE_DIR, channel)
    context = await _launch_persistent(pw, profile, headed=headed, channel=channel)
    if extra:
        await context.add_init_script(_session_storage_init_script(extra))
    return _Opened(context=context, browser=context.browser, persistent=True)


async def _open_storage_state_context(pw, channel: str) -> _Opened:
    """Warm from a storage_state blob when there is no Chrome profile yet.

    This is the path a freshly-hydrated container takes (hydrate writes a
    `.session.json` storage_state, not a Chrome user-data dir), so it is the one
    that actually runs in production — and it must honour `HEADLESS` exactly like
    the persistent path. It used to hardcode `headless=True`, which is why the
    anti-bot channels stalled even when the worker was told to run headed: the
    real Chrome, headless, is what Akamai/PerimeterX drop. Headed under a virtual
    display (Xvfb) loads the page and rotates the cookie (verified on the VM).
    """
    extra = load_extra_state(channel)
    # Container sandbox flags first (Chrome won't launch as non-root pwuser
    # without them), then any per-channel extras like noon's --disable-http2.
    launch_args = [*CONTAINER_CHROME_ARGS, *(_CHANNEL_LAUNCH_ARGS.get(channel) or [])]
    headed = not settings.HEADLESS
    try:
        browser = await pw.chromium.launch(
            headless=not headed, channel="chrome", args=launch_args
        )
    except Exception as exc:
        # Bundled Chromium exists on a dev laptop but not in the container image
        # (branded Chrome only); if it also fails, surface the real branded-Chrome
        # error rather than the misleading "chromium-1234 not found".
        try:
            browser = await pw.chromium.launch(headless=not headed, args=launch_args)
        except Exception:
            raise exc from None
    state = _storage_state_path(channel)
    context = await browser.new_context(
        **context_kwargs(
            storage_state=str(state) if state.exists() else None,
            headed=headed,
        )
    )
    if extra:
        await context.add_init_script(_session_storage_init_script(extra))
    return _Opened(context=context, browser=browser, persistent=False)


async def _first_page(opened: _Opened):
    if opened.context.pages:
        return opened.context.pages[0]
    return await opened.context.new_page()


async def probe_channel(channel: str) -> ProbeResult:
    """Load the channel's probe page and return cookies, headers, persisted state.

    Raises `NeedsHumanLogin` when the stored session lands on a login page.
    """
    probe: ChannelProbe = CHANNEL_PROBES[channel]
    captured: dict[str, str] = {}

    async with async_playwright() as pw:
        profile = chrome_profile_dir(settings.STORAGE_STATE_DIR, channel)
        if profile.exists():
            opened = await _open_context(pw, channel, headed=not settings.HEADLESS)
        else:
            opened = await _open_storage_state_context(pw, channel)
        try:
            page = await _first_page(opened)

            def _on_request(request) -> None:
                if probe.match not in request.url:
                    return
                headers = request.headers
                # Careem: the finances page fires an OIDC identity-token call
                # (aud=com.careem.internal) AND the real partner-API calls, which
                # carry a business-audience bearer. Log every saturn-ext request's
                # aud+url to identify them, and prefer capturing a NON-identity
                # (partner-API) bearer over the identity one.
                if channel == "careem" and "authorization" in headers:
                    aud = _bearer_aud(headers.get("authorization", ""))
                    logger.info(
                        "careem saturn-ext: aud=%s %s %s",
                        aud,
                        request.method,
                        request.url[:120],
                    )
                    if aud and aud != "com.careem.internal":
                        captured.clear()
                        captured.update(headers)
                    elif "authorization" not in captured:
                        captured.update(headers)  # provisional identity token
                    return
                if "authorization" in captured:
                    return
                # Non-careem: capture the first match, preferring a bearer one.
                if not captured or "authorization" in headers:
                    captured.clear()
                    captured.update(headers)

            page.on("request", _on_request)
            try:
                # `commit` (fires on the first response byte, when the anti-bot
                # edge sets its cookie) rather than `domcontentloaded`: noon's
                # Akamai SPA holds the connection open and never fired
                # `domcontentloaded` within 60s even over HTTP/1.1, aborting the
                # warm. The warm only needs the rotated edge cookie, which is set
                # at commit; the `sleep(3)` after lets the sensor JS run.
                await page.goto(
                    probe.probe_url,
                    wait_until="commit",
                    timeout=max(settings.PROBE_TIMEOUT_MS, 45_000),
                )
            except Exception as exc:  # noqa: BLE001 — playwright errors are lazy
                # A single-page-app whose root never fires `domcontentloaded`
                # (or a slow Akamai edge) must not abort the warm: if the page
                # actually navigated, the request already went out under the
                # anti-bot edge and the load-bearing cookie has rotated — which
                # is the whole point of the warm, not a fully-painted page.
                # `_url_looks_logged_out` below still catches a real login
                # redirect. But if nothing committed (still about:blank), the
                # navigation truly failed — re-raise so a genuine breakage on
                # any channel is not masked as a silent empty snapshot.
                if not page.url or page.url == "about:blank":
                    raise
                logger.warning(
                    "%s probe goto did not settle (%s); snapshotting the "
                    "rotated cookies anyway",
                    channel,
                    str(exc).splitlines()[0][:120],
                )
            await asyncio.sleep(3)
            # Careem's finances page bounces through an OAuth code-exchange
            # (`/saturn-ext/auth/identity?code=…`) before the authenticated
            # `/api/saturn-ext/` call fires, so the Authorization header the
            # provider replays is often not captured on the first settle. Give the
            # exchange a moment and re-load the probe page until the header lands.
            if channel == "careem":
                # Reload until we hold a PARTNER-API bearer, not just the OIDC
                # identity token the exchange call carries. Try a couple of known
                # business surfaces so a saturn-ext API call actually fires.
                surfaces = [
                    probe.probe_url,
                    "https://partners.careem.com/saturn-ext/merchant/orders",
                    "https://partners.careem.com/home",
                ]
                for i in range(6):
                    await asyncio.sleep(4)
                    cap_aud = _bearer_aud(captured.get("authorization", ""))
                    if cap_aud and cap_aud != "com.careem.internal":
                        break
                    try:
                        await page.goto(
                            surfaces[i % len(surfaces)],
                            wait_until="commit",
                            timeout=30_000,
                        )
                    except Exception:  # noqa: BLE001 — SPA lazy nav; keep polling
                        pass
                logger.info(
                    "careem: captured token aud=%s",
                    _bearer_aud(captured.get("authorization", "")),
                )
            result = await _snapshot_context(channel, opened.context, page, captured)
        finally:
            await opened.close()

    if _url_looks_logged_out(result.final_url):
        raise NeedsHumanLogin(
            f"{channel} session is stale (landed on {result.final_url}). "
            f"Run: aggregator-bootstrap login --channel {channel}"
        )
    return result


def _url_looks_logged_out(url: str) -> bool:
    lower = url.lower()
    return any(w in lower for w in ("login", "signin", "identity", "auth", "passport"))


async def login_interactive(channel: str) -> ProbeResult:
    """Open real Chrome, wait for the operator to sign in, persist state.

    Chrome runs as a normal process for the Cloudflare check. Playwright
    attaches over CDP only after a session cookie shows up, then captures
    storage_state. Closing the window before that aborts the login.
    """
    if channel not in CHANNEL_PROBES:
        raise NeedsHumanLogin(f"unknown channel {channel}")

    start_url = LOGIN_START_URLS[channel]
    probe: ChannelProbe = CHANNEL_PROBES[channel]
    captured: dict[str, str] = {}
    profile = chrome_profile_dir(settings.STORAGE_STATE_DIR, channel)
    port = free_debug_port()
    chrome = _spawn_chrome(profile=profile, port=port, url=start_url)

    print(  # noqa: T201 — the operator is sitting at this window
        f"\n=== {channel} ===\n"
        f"Google Chrome just opened as a normal window (nothing is controlling\n"
        f"it — that is what lets Cloudflare pass). If it asks you to wait or\n"
        f"tick a box, do that, then sign in.\n"
        f"Leave the window open. I attach only after you are in the portal\n"
        f"(up to {_LOGIN_WAIT_SECONDS // 60} minutes) and then capture the session.\n",
        flush=True,
    )

    try:
        await _wait_for_cdp(port)
        await asyncio.sleep(_UNATTACHED_SECONDS)

        started = time.monotonic()
        deadline = started + _LOGIN_WAIT_SECONDS
        attached = False
        browser = context = page = None

        async with async_playwright() as pw:
            while time.monotonic() < deadline:
                if chrome.poll() is not None:
                    raise NeedsHumanLogin(
                        f"{channel}: the Chrome window was closed before login finished"
                    )
                names = chrome_cookie_names(profile)
                ready = _session_cookies_present(channel, names)
                past_challenge = "cf_clearance" in names
                sqlite_silent = not names and (time.monotonic() - started) > 180
                if not attached and (ready or past_challenge or sqlite_silent):
                    try:
                        browser, context, page = await _connect_cdp(pw, port)
                        attached = True

                        def _on_request(request) -> None:
                            if probe.match in request.url:
                                captured.update(request.headers)

                        page.on("request", _on_request)
                        logger.info("%s: attached to Chrome after challenge", channel)
                    except Exception as exc:  # noqa: BLE001
                        logger.info("CDP attach retry: %s", exc)
                        await asyncio.sleep(2)
                        continue

                if attached and page is not None and not _page_is_closed(page):
                    try:
                        if await page_looks_authenticated(channel, page):
                            break
                    except Exception as exc:  # noqa: BLE001
                        logger.info("auth probe blip: %s", exc)
                await asyncio.sleep(2)
            else:
                raise NeedsHumanLogin(
                    f"{channel}: timed out waiting for a logged-in session"
                    + (f" at {page.url}" if page is not None else "")
                )

            assert context is not None and page is not None
            # Snapshot the live session first. The reporting probe is how we
            # lift API headers; it is allowed to fail — cookies already matter.
            result = await _snapshot_context(channel, context, page, captured)
            try:
                await page.goto(
                    probe.probe_url,
                    wait_until="domcontentloaded",
                    timeout=max(settings.PROBE_TIMEOUT_MS, 90_000),
                )
                await asyncio.sleep(4)
                if await page_looks_authenticated(channel, page):
                    result = await _snapshot_context(channel, context, page, captured)
                else:
                    logger.warning(
                        "%s: probe page is %s; keeping the login snapshot",
                        channel,
                        page.url,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s: probe navigation failed after login; keeping snapshot (%s)",
                    channel,
                    exc,
                )
    finally:
        _stop_chrome(chrome)

    print(f"Captured {channel} session ({len(result.cookies)} cookies).", flush=True)
    return result


async def login_with_account(
    channel: str,
    *,
    email: str,
    password: str = "",
    mailbox: dict[str, Any] | None = None,
) -> ProbeResult:
    """Headed Chrome, drive stored login after anti-bot, then capture.

    Same unattached-Chrome start as `login_interactive` so CF/Akamai sees a
    real browser. Wired channels:

    - **deliveroo** — email+password once the login form is visible
    - **noon** — email → Graph OTP via `login_noon` (needs mailbox-auth)
    - **talabat** — email+password → Graph OTP via `login_talabat`
    - **careem** — email → Graph OTP via `login_careem` (needs mailbox-auth)
    """
    if channel not in ("deliveroo", "noon", "talabat", "careem"):
        raise NeedsHumanLogin(
            f"{channel} auto-login is not wired yet; run `login --channel "
            f"{channel}` headed, or store the recipe and wait for that channel's "
            "fill helper."
        )
    if channel == "deliveroo" and (not email or not password):
        raise NeedsHumanLogin(
            "Deliveroo account has no email/password in the DB. "
            "Run `store-account --channel deliveroo` first."
        )
    if channel == "noon" and not email:
        raise NeedsHumanLogin(
            "Noon account has no email in the DB. Save it on Admin → Logins."
        )
    if channel == "talabat":
        if not email or not password:
            raise NeedsHumanLogin(
                "Talabat account has no email/password in the DB. "
                "Run `store-account --channel talabat` first."
            )
        if not mailbox or not str((mailbox or {}).get("refresh_token") or "").strip():
            raise NeedsHumanLogin(
                "Talabat needs a linked Graph mailbox for OTP. "
                "Run: aggregator-bootstrap mailbox-auth --channel talabat"
            )
    if channel == "careem":
        if not email:
            raise NeedsHumanLogin(
                "Careem account has no email in the DB. Save it on Admin → Logins."
            )
        if not mailbox or not str((mailbox or {}).get("refresh_token") or "").strip():
            raise NeedsHumanLogin(
                "Careem needs a linked Graph mailbox for OTP. "
                "Run: aggregator-bootstrap mailbox-auth --channel careem"
            )
    if channel not in CHANNEL_PROBES:
        raise NeedsHumanLogin(f"unknown channel {channel}")

    start_url = LOGIN_START_URLS[channel]
    probe: ChannelProbe = CHANNEL_PROBES[channel]
    captured: dict[str, str] = {}
    profile = chrome_profile_dir(settings.STORAGE_STATE_DIR, channel)
    port = free_debug_port()
    chrome = _spawn_chrome(profile=profile, port=port, url=start_url)
    filled = False

    if channel == "noon":
        hint = (
            f"Google Chrome opened on Noon RMS. I fill the email and poll the\n"
            f"linked Graph mailbox for the OTP (up to {_LOGIN_WAIT_SECONDS // 60} "
            f"minutes). If Akamai challenges, complete it in the window.\n"
        )
    elif channel == "talabat":
        hint = (
            f"Google Chrome opened on Talabat Partner. I fill email/password and\n"
            f"poll the linked Graph mailbox for the OTP (up to "
            f"{_LOGIN_WAIT_SECONDS // 60} minutes). Complete any PerimeterX\n"
            f"challenge in the window if it appears.\n"
        )
    elif channel == "careem":
        hint = (
            f"Google Chrome opened on Careem Partners. I pick the Email method,\n"
            f"fill the email and poll the linked Graph mailbox for the OTP (up to\n"
            f"{_LOGIN_WAIT_SECONDS // 60} minutes). If reCAPTCHA challenges, "
            f"complete it in the window.\n"
        )
    else:
        hint = (
            f"Google Chrome opened. If Cloudflare asks you to wait or tick a box,\n"
            f"do that. I fill the email/password from the stored account once the\n"
            f"login form is visible (up to {_LOGIN_WAIT_SECONDS // 60} minutes).\n"
        )
    print(f"\n=== {channel} (auto) ===\n{hint}", flush=True)  # noqa: T201

    try:
        await _wait_for_cdp(port)
        await asyncio.sleep(_UNATTACHED_SECONDS)

        started = time.monotonic()
        deadline = started + _LOGIN_WAIT_SECONDS
        attached = False
        browser = context = page = None

        async with async_playwright() as pw:
            while time.monotonic() < deadline:
                if chrome.poll() is not None:
                    raise NeedsHumanLogin(
                        f"{channel}: the Chrome window was closed before login finished"
                    )
                names = chrome_cookie_names(profile)
                ready = _session_cookies_present(channel, names)
                past_challenge = "cf_clearance" in names
                waited_out_cf = (time.monotonic() - started) > 45
                if not attached and (ready or past_challenge or waited_out_cf):
                    try:
                        browser, context, page = await _connect_cdp(pw, port)
                        attached = True

                        def _on_request(request) -> None:
                            if probe.match in request.url:
                                captured.update(request.headers)

                        page.on("request", _on_request)
                        logger.info("%s: attached to Chrome for auto-login", channel)
                        if channel == "noon" and not filled:
                            logger.info("%s: driving email + Graph OTP", channel)
                            page = await login_noon(
                                context,
                                mailbox=mailbox,
                                email=email,
                                page=page,
                            )
                            filled = True
                        elif channel == "talabat" and not filled:
                            logger.info(
                                "%s: driving email/password + Graph OTP", channel
                            )
                            page = await login_talabat(
                                context,
                                mailbox=mailbox,
                                email=email,
                                password=password,
                                page=page,
                            )
                            filled = True
                        elif channel == "careem" and not filled:
                            logger.info("%s: driving email + Graph OTP", channel)
                            page = await login_careem(
                                context,
                                mailbox=mailbox,
                                email=email,
                                password=password,
                                page=page,
                            )
                            filled = True
                    except Exception as exc:  # noqa: BLE001
                        logger.info("CDP attach retry: %s", exc)
                        await asyncio.sleep(2)
                        continue

                if attached and page is not None and not _page_is_closed(page):
                    try:
                        if await page_looks_authenticated(channel, page):
                            break
                        if (
                            channel == "deliveroo"
                            and not filled
                            and await deliveroo_login_form_visible(page)
                        ):
                            logger.info("%s: filling stored credentials", channel)
                            await fill_deliveroo_login(
                                page, email=email, password=password
                            )
                            filled = True
                    except Exception as exc:  # noqa: BLE001
                        logger.info("auto-login step blip: %s", exc)
                await asyncio.sleep(2)
            else:
                raise NeedsHumanLogin(
                    f"{channel}: timed out waiting for a logged-in session"
                    + (f" at {page.url}" if page is not None else "")
                    + ("" if filled else " (login form never appeared)")
                )

            assert context is not None and page is not None
            result = await _snapshot_context(channel, context, page, captured)
            try:
                await page.goto(
                    probe.probe_url,
                    wait_until="domcontentloaded",
                    timeout=max(settings.PROBE_TIMEOUT_MS, 90_000),
                )
                await asyncio.sleep(4)
                if await page_looks_authenticated(channel, page):
                    result = await _snapshot_context(channel, context, page, captured)
                else:
                    logger.warning(
                        "%s: probe page is %s; keeping the login snapshot",
                        channel,
                        page.url,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s: probe navigation failed after login; keeping snapshot (%s)",
                    channel,
                    exc,
                )
    finally:
        _stop_chrome(chrome)

    print(f"Captured {channel} session ({len(result.cookies)} cookies).", flush=True)
    return result


async def ensure_session(channel: str) -> ProbeResult:
    """Probe the channel. A stale session is a human login, not an OTP poll."""
    return await probe_channel(channel)
