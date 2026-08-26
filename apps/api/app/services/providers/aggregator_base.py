"""What every delivery-marketplace client looks like from inside the ingest.

The aggregators publish no partner API this shop is on, so each provider speaks
the marketplace's own *console* API the way the browser does: it replays a
session a bootstrap captured — cookies (including the load-bearing anti-bot
cookie), tokens, and the exact header fingerprint — and never opens a browser on
the hourly path. This base holds the parts every channel shares: assembling that
fingerprint onto a request, telling an auth failure (the session is dead, only a
browser can save it) apart from a provider being slow (retry later), and the
choice of transport.

**Transport and authenticity.** Careem and Deliveroo have no bot wall, so plain
`httpx` is enough. Talabat (PerimeterX) and Noon (Akamai) fingerprint the TLS
ClientHello itself, so a Python client is flagged even with perfect cookies —
those set `uses_tls_impersonation` and, where `curl_cffi` is installed, go out
with a Chrome ClientHello. Absent that library the base falls back to `httpx`
and says so once, rather than failing to import.

Providers return the channel-neutral DTOs in `services/aggregators/normalized`;
nothing above this line learns a marketplace's vocabulary.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services.aggregators.normalized import FinanceResult, SalesResult
from app.services.aggregators.session_store import LoadedSession

logger = logging.getLogger(__name__)

try:  # optional — only the anti-bot channels need it
    from curl_cffi import requests as curl_requests  # type: ignore

    _HAS_CURL_CFFI = True
except Exception:  # noqa: BLE001 - absence is a supported state, not an error
    curl_requests = None  # type: ignore
    _HAS_CURL_CFFI = False

_warned_no_curl = False


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

    # ── fingerprint assembly ────────────────────────────────────────────────
    def build_headers(
        self, session: LoadedSession, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        """The captured header profile, plus the replayed cookie, plus per-call extras.

        The profile is sent verbatim — changing the UA invalidates the anti-bot
        cookie that was minted under it. `extra` is for the per-request headers a
        provider adds (a bearer, a CSRF token, an operation name).
        """
        headers: dict[str, str] = dict(session.header_profile or {})
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
    ) -> Any:
        """One call, returning the transport's raw response (JSON or CSV/text).

        Used directly by channels whose export is not JSON (Deliveroo CSV). Picks
        the TLS-impersonating transport for the anti-bot channels when available.
        """
        merged = self.build_headers(session, headers)
        try:
            if self.uses_tls_impersonation and _HAS_CURL_CFFI:
                return await self._curl_request(
                    method, url, merged, params, json_body, data
                )
            self._warn_if_impersonation_wanted()
            return await self._httpx_request(
                method, url, merged, params, json_body, data
            )
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
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout, http2=True) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
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
    ) -> Any:
        async with curl_requests.AsyncSession() as client:  # type: ignore[union-attr]
            return await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                impersonate=self.impersonate_target,
                timeout=self._timeout,
            )

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
    async def fetch_finance(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> FinanceResult:
        """Statements and payouts published in the window."""
