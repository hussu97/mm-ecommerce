"""What every delivery-marketplace client looks like from inside the ingest.

The aggregators publish no partner API this shop is on, so each provider speaks
the marketplace's own *console* API the way the browser does: it replays a
session a bootstrap captured — cookies (including the load-bearing anti-bot
cookie), tokens, and the exact header fingerprint — and never opens a browser on
the hourly path. This base holds the parts every channel shares: assembling that
fingerprint onto a request, telling an auth failure (the session is dead, only a
browser can save it) apart from a provider being slow (retry later), and the
choice of transport.

**Transport and authenticity.** Deliveroo has no bot wall, so plain `httpx` is
enough. Talabat (PerimeterX), Noon (Akamai) and Careem (Cloudflare) fingerprint
the TLS ClientHello itself, so a Python client is flagged even with perfect
cookies — those set `uses_tls_impersonation` and, where `curl_cffi` is installed,
go out with a Chrome ClientHello. Absent that library the base falls back to
`httpx` and says so once, rather than failing to import. Cloudflare additionally
rotates a short-lived edge cookie; a channel behind it (Careem) refreshes that in
place on a 401 rather than reading the rotation as a dead session — see
`_cf_refresh_and_retry`.

Providers return the channel-neutral DTOs in `services/aggregators/normalized`;
nothing above this line learns a marketplace's vocabulary.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services.aggregators.normalized import (
    FinanceResult,
    PayoutsResult,
    SalesResult,
    StatementsResult,
)
from app.services.aggregators.session_store import LoadedSession

logger = logging.getLogger(__name__)

try:  # optional — only the anti-bot channels need it
    from curl_cffi import CurlMime  # type: ignore
    from curl_cffi import requests as curl_requests  # type: ignore

    _HAS_CURL_CFFI = True
except Exception:  # noqa: BLE001 - absence is a supported state, not an error
    curl_requests = None  # type: ignore
    CurlMime = None  # type: ignore
    _HAS_CURL_CFFI = False

_warned_no_curl = False

#: Chrome 151 on Windows — the same UA Foodics uses. Applied only when the
#: captured profile did not carry a User-Agent, so a cookie minted under a
#: real browser UA is never overwritten.
_CHROME_MAJOR = "151"
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
_ACCEPT_LANGUAGE = "en-AE,en;q=0.9,ar-AE;q=0.8,ar;q=0.7"

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Cloudflare's short-lived edge cookies. `__cf_bm` (~30 min) is the bot-management
#: cookie and `cf_clearance` (~1 h, IP-bound) the challenge-clearance one. A
#: statically-replayed jar carries these long after they expire, and once they do
#: the Cloudflare edge answers a bare 401 that looks exactly like a dead session.
#: A channel behind Cloudflare (Careem) refreshes these in-process — see
#: `_cf_refresh_url` and `_cf_refresh_and_retry`.
_CLOUDFLARE_COOKIE_NAMES = frozenset({"__cf_bm", "cf_clearance"})

#: Longest a `Retry-After` may hold a call before we give up and treat the channel
#: as unavailable (F-AGG-7). The header is uncapped and a marketplace can ask for
#: minutes; honouring that inside the sweep is what wedged it — and the run row is
#: better failed and retried next tick than blocked for a `Retry-After` of 600s.
_RETRY_AFTER_MAX_SECONDS = 30.0


class _RateLimiter:
    """One token every `1/rate` seconds, process-wide per client instance."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_at = time.monotonic() + self._interval


def _parse_cookie_header(cookie: str) -> dict[str, str]:
    """A `name=value; …` Cookie header back into a jar dict (first value wins)."""
    jar: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and name not in jar:
            jar[name] = value
    return jar


def _retry_after_seconds(response: Any, *, default: float = 2.0) -> float:
    raw = None
    headers = getattr(response, "headers", None) or {}
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        return default
    try:
        return max(float(raw), 0.5)
    except (TypeError, ValueError):
        return default


class AggregatorUnavailableError(RuntimeError):
    """The marketplace could not be reached, or the fault is plainly theirs.

    The retry-later signal — a timeout, a 5xx, a network drop. Distinct from an
    auth failure: retrying this does not mint a second credential or lock an
    account, it just waits for the other end to recover.
    """


class AggregatorAuthError(RuntimeError):
    """The session no longer authenticates — only a browser bootstrap can fix it.

    Raised for a 401/403 or a bot challenge. The ingest catches it and flips the
    session to `needs_bootstrap`; it must never be retried in a loop, because a
    dead cookie retried fast is how an account gets locked.
    """


class BaseAggregatorClient(ABC):
    """Everything a marketplace client must be to be ingestible."""

    #: Matches `AGGREGATOR_CHANNELS` and the `channel` column.
    channel: str
    #: Whether this channel's bot wall fingerprints TLS. Set on Talabat/Noon.
    uses_tls_impersonation: bool = False
    #: The Chrome build `curl_cffi` impersonates; kept alongside the header
    #: profile's UA so the two agree.
    impersonate_target: str = "chrome"

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = timeout or settings.AGGREGATOR_TIMEOUT_SECONDS
        self._limiter = _RateLimiter(settings.AGGREGATOR_REQUESTS_PER_SECOND)

    # ── fingerprint assembly ────────────────────────────────────────────────
    def build_headers(
        self, session: LoadedSession, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        """The captured header profile, plus the replayed cookie, plus per-call extras.

        The profile is sent verbatim — changing the UA invalidates the anti-bot
        cookie that was minted under it. Missing UA / Accept-Language are filled
        with the UAE Chrome defaults so a thin capture still looks like the
        console. `extra` is for the per-request headers a provider adds.
        """
        headers: dict[str, str] = dict(session.header_profile or {})
        lower = {k.lower(): k for k in headers}
        if "user-agent" not in lower:
            headers["User-Agent"] = _CHROME_UA
        if "accept-language" not in lower:
            headers["Accept-Language"] = _ACCEPT_LANGUAGE
        if "sec-ch-ua" not in lower:
            headers["sec-ch-ua"] = (
                f'"Google Chrome";v="{_CHROME_MAJOR}", '
                f'"Chromium";v="{_CHROME_MAJOR}", "Not A(Brand";v="8"'
            )
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = '"Windows"'
        cookie = self.cookie_header(session.cookies)
        if cookie:
            headers["Cookie"] = cookie
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def cookie_header(cookies: dict[str, str]) -> str:
        """A `name=value; ...` cookie string, order preserved from capture."""
        return "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())

    def _is_auth_failure(self, response: Any) -> bool:
        """Whether a response says the session is dead. Overridable per channel.

        Default is the plain 401/403. A channel whose bot wall answers 200 with
        a challenge body (PerimeterX, Akamai) overrides this to read the body.
        """
        return getattr(response, "status_code", None) in (401, 403)

    def _cf_refresh_url(self) -> str | None:
        """Origin to warm Cloudflare's edge cookie on, or None if not fronted.

        A channel behind Cloudflare (Careem) returns this; on a 401 the transport
        makes one GET here inside the same impersonated session to pick up a fresh
        `__cf_bm`/`cf_clearance` and retries, rather than treating a routine edge-
        cookie rotation as a dead session. Every other channel returns None and
        keeps the old behaviour.
        """
        return None

    # ── transport ───────────────────────────────────────────────────────────
    async def request_json(
        self,
        session: LoadedSession,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        data: Any | None = None,
    ) -> Any:
        """Make one authenticated call and return parsed JSON.

        Raises `AggregatorAuthError` when the session is dead and
        `AggregatorUnavailableError` when the fault is transient — the two
        signals the ingest routes on.
        """
        response = await self.request_raw(
            session,
            method,
            url,
            headers=headers,
            params=params,
            json_body=json_body,
            data=data,
        )
        if self._is_auth_failure(response):
            raise AggregatorAuthError(
                f"{self.channel} returned {getattr(response, 'status_code', '?')} "
                "— session no longer authenticates"
            )
        status = getattr(response, "status_code", 0)
        if status >= 500:
            raise AggregatorUnavailableError(f"{self.channel} returned {status}")
        if status >= 400:
            raise AggregatorUnavailableError(
                f"{self.channel} returned {status}: "
                f"{getattr(response, 'text', '')[:200]}"
            )
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001 - a non-JSON 200 is a provider fault
            raise AggregatorUnavailableError(
                f"{self.channel} returned non-JSON body"
            ) from exc

    async def request_raw(
        self,
        session: LoadedSession,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        data: Any | None = None,
        files: Any | None = None,
        timeout: float | None = None,
    ) -> Any:
        """One call, returning the transport's raw response (JSON or CSV/text).

        Used directly by channels whose export is not JSON (Deliveroo CSV) or that
        upload files (`files=` -> multipart/form-data, e.g. a Careem product image).
        Picks the TLS-impersonating transport for the anti-bot channels when
        available.
        """
        merged = self.build_headers(session, headers)
        last_response: Any = None
        call_timeout = timeout if timeout is not None else self._timeout
        try:
            for attempt in range(2):
                await self._limiter.acquire()
                if self.uses_tls_impersonation and _HAS_CURL_CFFI:
                    last_response = await self._curl_request(
                        method,
                        url,
                        merged,
                        params,
                        json_body,
                        data,
                        files=files,
                        timeout=call_timeout,
                    )
                else:
                    self._warn_if_impersonation_wanted()
                    last_response = await self._httpx_request(
                        method,
                        url,
                        merged,
                        params,
                        json_body,
                        data,
                        files=files,
                        timeout=call_timeout,
                    )
                status = getattr(last_response, "status_code", 0)
                if status in _RETRY_STATUSES and attempt == 0:
                    wait_for = _retry_after_seconds(last_response)
                    if wait_for > _RETRY_AFTER_MAX_SECONDS:
                        # An uncapped Retry-After would idle the whole sweep (and,
                        # before F-AGG-7, a pooled connection) for minutes. Fail the
                        # call cleanly instead; the run is marked unavailable and the
                        # next scheduled tick retries.
                        raise AggregatorUnavailableError(
                            f"{self.channel} asked to retry after {wait_for:.0f}s "
                            f"(> {_RETRY_AFTER_MAX_SECONDS:.0f}s cap) — treating as "
                            "unavailable"
                        )
                    await asyncio.sleep(wait_for)
                    continue
                return last_response
            return last_response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise AggregatorUnavailableError(
                f"{self.channel} unreachable: {exc}"
            ) from exc

    async def _httpx_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_body: Any | None,
        data: Any | None,
        *,
        files: Any | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=timeout or self._timeout, http2=True
        ) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                files=files,
                content=data if isinstance(data, (bytes, str)) else None,
                data=data if not isinstance(data, (bytes, str)) else None,
            )

    async def _curl_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_body: Any | None,
        data: Any | None,
        *,
        files: Any | None = None,
        timeout: float | None = None,
    ) -> Any:
        # Hand curl_cffi the cookie JAR, not a pre-folded `Cookie` header: under
        # impersonation it emits the Cookie header itself, in the impersonated
        # browser's exact header ORDER. A manually-folded Cookie header lands out
        # of order, and a Cloudflare-fronted API (Careem) rejects the mismatched
        # fingerprint with a bare 401 — verified live: identical request, cookies
        # as param 200 vs cookies as header 401. Talabat/Noon are unaffected (they
        # do not gate on this), so the split is safe for every impersonated channel.
        headers = dict(headers)
        cookies: dict[str, str] | None = None
        for key in [k for k in headers if k.lower() == "cookie"]:
            cookies = _parse_cookie_header(headers.pop(key))
        # curl_cffi does not accept httpx-style `files=`; it wants a CurlMime on
        # its `multipart` param. Convert the same `{field: (filename, bytes,
        # content_type)}` shape so callers stay transport-agnostic. Verified live
        # against Careem's catalog-products-images endpoint (201 Created).
        multipart = None
        if files:
            multipart = CurlMime()  # type: ignore[operator]
            for field, spec in files.items():
                filename, content, content_type = (
                    spec if isinstance(spec, (tuple, list)) else (field, spec, None)
                )
                multipart.addpart(
                    name=field,
                    filename=filename,
                    content_type=content_type or "application/octet-stream",
                    data=content,
                )
        async with curl_requests.AsyncSession() as client:  # type: ignore[union-attr]
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                multipart=multipart,
                cookies=cookies,
                impersonate=self.impersonate_target,
                timeout=timeout or self._timeout,
            )
            # A Cloudflare-fronted channel whose edge cookie has rotated answers a
            # bare 401. Warm a fresh one inside this same session and retry once,
            # before the caller reads the 401 as a dead session and pays for a
            # headed re-login. Skipped for uploads — a CurlMime is not reusable.
            refresh_url = self._cf_refresh_url()
            if (
                refresh_url
                and multipart is None
                and getattr(response, "status_code", None) in (401, 403)
            ):
                retried = await self._cf_refresh_and_retry(
                    client,
                    refresh_url,
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json_body=json_body,
                    data=data,
                    cookies=cookies,
                    timeout=timeout or self._timeout,
                )
                if retried is not None:
                    return retried
            return response

    async def _cf_refresh_and_retry(
        self,
        client: Any,
        refresh_url: str,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_body: Any | None,
        data: Any | None,
        cookies: dict[str, str] | None,
        timeout: float,
    ) -> Any | None:
        """Warm Cloudflare's edge cookie in-session and retry the call once.

        Best effort: a GET to the origin lets the edge re-issue `__cf_bm` /
        `cf_clearance` into this session's jar; the call is then retried with the
        stale edge cookies dropped from the replayed jar so the fresh ones win
        (curl_cffi merges the session jar with per-request cookies). Returns the
        retried response, or None to fall back to the original 401 — when the
        edge issues no fresh cookie (a real challenge) or the session is genuinely
        dead, which still escalates to a headed re-login exactly as before.
        """
        try:
            await client.get(
                refresh_url,
                headers=headers,
                cookies=cookies,
                impersonate=self.impersonate_target,
                timeout=timeout,
            )
            fresh = {
                name
                for name in dict(client.cookies)
                if name.lower() in _CLOUDFLARE_COOKIE_NAMES
            }
            if not fresh:
                return None
            retry_cookies = {
                k: v
                for k, v in (cookies or {}).items()
                if k.lower() not in _CLOUDFLARE_COOKIE_NAMES
            }
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                cookies=retry_cookies,
                impersonate=self.impersonate_target,
                timeout=timeout,
            )
            if getattr(response, "status_code", None) in (401, 403):
                return None
            logger.info(
                "%s: refreshed Cloudflare edge cookie in-process and recovered a 401",
                self.channel,
            )
            return response
        except Exception as exc:  # noqa: BLE001 — best effort; fall back to the 401
            logger.debug("%s: Cloudflare cookie refresh failed: %s", self.channel, exc)
            return None

    def _warn_if_impersonation_wanted(self) -> None:
        global _warned_no_curl
        if self.uses_tls_impersonation and not _HAS_CURL_CFFI and not _warned_no_curl:
            _warned_no_curl = True
            logger.warning(
                "%s wants TLS impersonation but curl_cffi is not installed; "
                "falling back to httpx, which its bot wall may flag",
                self.channel,
            )

    # ── the interface the ingest calls ──────────────────────────────────────
    @abstractmethod
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        """Orders placed in the window, as channel-neutral DTOs."""

    @abstractmethod
    async def fetch_statements(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> StatementsResult:
        """Settlement documents published in the window (not payouts)."""

    @abstractmethod
    async def fetch_payouts(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> PayoutsResult:
        """Bank/transfer payouts published in the window (not statements)."""

    async def fetch_finance(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> FinanceResult:
        """Compat wrapper: statements then payouts from the distinct methods."""
        statements = await self.fetch_statements(session, since=since, until=until)
        payouts = await self.fetch_payouts(session, since=since, until=until)
        notes = [n for n in (statements.truncation_note, payouts.truncation_note) if n]
        return FinanceResult(
            statements=statements.statements,
            payouts=payouts.payouts,
            truncation_note=" | ".join(notes) if notes else None,
        )
