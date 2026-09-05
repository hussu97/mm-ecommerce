"""Deliveroo Partner Hub console API, as the SPA calls it.

Deliveroo publishes no partner API this shop is on. The Partner Hub login is
`POST /api/session` with the stored email/password; that returns an
`access_token` JWT (identity.deliveroo.com, `amr=pwd`). The SPA stores it as
the `token` cookie *and* sends `Authorization: Bearer`. Cookie-only replay of
an expired Chrome capture is why earlier VM sweeps 401'd; Bearer + a fresh
JWT is what `/api/session` and the restaurant/invoice endpoints accept.

Two data paths, confirmed against the live hub from the production VM:

- **Sales** — `GET /api/restaurants/{id}/orders?start_date&end_date` lists the
  window (one row per order: number, status, fils amount, placed_at). Line
  items come from `GET /api/orders/{order_id}`. Restaurant ids are the same
  outlet ids seeded in `aggregator_branch_map` (693359 / 693360 / 693361).

- **Finance** — `GET /api/invoices?org_id=` returns the published statements
  (totals, period, due date, download links). Per-order settlement lines still
  come from `statement_csv` when Cloudflare lets the download through; a 403
  on the file is truncation, not a dead session.

The reconstructed `POST /api/reporting_platform/reports` path 404/401s; the
hub never served reports that way to this token.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money_or_none
from app.models.aggregator import (
    CHANNEL_DELIVEROO,
    GRAIN_LINE,
    SESSION_LIVE,
    AggregatorBranchMap,
)
from app.models.base import utcnow
from app.services.aggregators.modifiers import expand_modifiers
from app.services.aggregators.normalized import (
    PayoutsResult,
    SalesResult,
    StandardOrder,
    StandardOrderItem,
    StandardPayout,
    StandardStatement,
    StandardStatementLine,
    StandardStatusEvent,
    StatementsResult,
)
from app.services.aggregators.session_store import LoadedSession
from app.services.aggregators.statement_docs import (
    StoredStatementInvoice,
    store_statement_invoice,
)
from app.services.providers._agg_parse import DUBAI_TZ as _BUSINESS_TZ
from app.services.providers._agg_parse import first_present as _first
from app.services.providers._agg_parse import parse_money as _num
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    AggregatorUnavailableError,
    BaseAggregatorClient,
)

logger = logging.getLogger(__name__)

_HUB = "https://partner-hub.deliveroo.com"
_API = f"{_HUB}/api"
_LOGIN_URL = f"{_API}/session"
_REFRESH_URL = f"{_API}/session/refresh"

#: The org and outlet ids are read from the DB ONLY — the org from
#: `aggregator_account.extras.org_id`, the outlets from the `aggregator_branch_map`
#: rows for this channel. There are deliberately NO hard-coded fallbacks: the old
#: `_DEFAULT_ORG_ID`/`_DEFAULT_RESTAURANT_IDS` duplicated DB data, went stale (they
#: never had the Karama outlet), and silently masked missing config as a wrong-scope
#: 200-but-empty pull. When the DB genuinely lacks this config the provider raises
#: `AggregatorUnavailableError` (loud, retry-later) instead of guessing.

#: Re-login this far before JWT `exp` so a sweep never presents an expired
#: Bearer. The identity token this login mints lasts under an hour.
_REFRESH_SKEW = timedelta(minutes=5)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _fils(value: Any) -> Decimal | None:
    """Partner Hub money: `{fractional: 4000}` is AED 40.00 (fils / 100)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        fractional = value.get("fractional")
        if fractional is None:
            return _num(value.get("formatted") or value.get("amount"))
        return money_or_none(Decimal(str(fractional)) / Decimal(100))
    return money_or_none(value)


def _as_list(payload: Any, *keys: str) -> list[Any]:
    """A list out of a payload that is either a bare list or wraps one under a
    known key (`reports`, `invoices`, `data`)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _raw_modifiers(item: dict[str, Any]) -> list[Any]:
    """Flatten Deliveroo's modifier/option payload to a list of option dicts.

    Two shapes are observed on the Partner Hub:

    1. Nested groups — ``item.modifiers = [{name, options: [{id, name, qty, price}]}]``
    2. Flat list  — ``item.options`` / ``item.addons`` / ``item.modifier_items``

    In either case the result is a flat list of individual option objects that
    ``expand_modifiers`` can digest.  The modifier-group name is intentionally
    discarded here; it is decorative for analytics and adding it would invent a
    second "option" row for the group itself.
    """
    groups = item.get("modifiers")
    if isinstance(groups, list) and groups:
        flat: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            inner = (
                group.get("options")
                or group.get("modifier_items")
                or group.get("items")
            )
            if isinstance(inner, list):
                flat.extend(inner)
            else:
                # Group entry has no sub-list — treat it as the option itself.
                flat.append(group)
        if flat:
            return flat
    for key in ("options", "addons", "modifier_items"):
        candidates = item.get(key)
        if isinstance(candidates, list) and candidates:
            return candidates
    return []


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_date(value: Any) -> date | None:
    """A `date` from an ISO string, a Deliveroo `Wed 3 Sep 2025` label, or a
    datetime — else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%a %d %b %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_to_business(value: str | None) -> datetime | None:
    """Deliveroo's statement CSV states delivery time in UTC; the shop books it
    to the Dubai (UTC+4) business day, so an evening order is not shunted a day
    back."""
    if not value or not value.strip():
        return None
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return _parse_dt(value)
    return naive.replace(tzinfo=timezone.utc).astimezone(_BUSINESS_TZ)


def _jwt_exp(token: str) -> datetime | None:
    try:
        payload = token.split(".")[1]
        pad = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + pad))
        exp = data.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except Exception:  # noqa: BLE001 — a malformed JWT is "no expiry", not a crash
        return None


def _restaurant_ids_from_login(body: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for company in body.get("restaurant_companies") or []:
        if not isinstance(company, dict):
            continue
        for row in company.get("restaurants") or []:
            if isinstance(row, dict) and row.get("id"):
                ids.append(str(row["id"]))
    return ids


def _outlet_ids_in(tokens: dict) -> bool:
    """Whether a token blob already carries this account's outlet ids."""
    raw = tokens.get("restaurant_ids") or tokens.get("restaurantIds")
    if isinstance(raw, list) and raw:
        return True
    if isinstance(raw, str) and raw.strip():
        return True
    extras = tokens.get("restaurants")
    return isinstance(extras, list) and bool(extras)


async def _outlet_ids_from_map(db: AsyncSession) -> list[str]:
    """This account's Deliveroo outlet ids from `aggregator_branch_map` — the DB
    row that already holds them, so the sweep does not depend on a code constant
    that (by construction) never had the Karama outlet."""
    rows = await db.scalars(
        select(AggregatorBranchMap.external_outlet_id).where(
            AggregatorBranchMap.channel == CHANNEL_DELIVEROO,
            AggregatorBranchMap.is_active.is_(True),
            AggregatorBranchMap.external_outlet_id.is_not(None),
        )
    )
    return [str(x) for x in rows if x]


#: Deliveroo's order-detail `timeline`, in lifecycle order, paired with the
#: normalized status word each key maps to. `_merge_order_detail` walks this to
#: build `StandardOrder.status_events`: a key whose timestamp is present emits one
#: tz-aware event, sequenced by its position here; an absent/null timestamp is a
#: step that has not happened yet, so it emits nothing. Deliveroo exposes the
#: richest per-order lifecycle of any channel, so it is captured in full (the
#: terminal delivered/cancelled step is appended separately, timed by its own
#: `delivered_at` / `cancelled_at`).
_TIMELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("placed_at", "placed"),
    ("accepted_at", "accepted"),
    ("confirmed_at", "confirmed"),
    ("prepare_for", "prepare_for"),
    ("delivery_picked_up_at", "picked_up"),
)

#: Terminal status words that close the lifecycle; the appended terminal event
#: uses the order's own `status` word, so only these emit a final step.
_TERMINAL_STATUSES = frozenset({"delivered", "cancelled", "canceled", "rejected"})


class DeliverooClient(BaseAggregatorClient):
    channel = CHANNEL_DELIVEROO
    # Partner Hub is Cloudflare-fronted. The JSON API tolerates plain httpx, but
    # the invoice FILE download (`/invoices/{id}/download`) answers httpx with a
    # Cloudflare 403 HTML interstitial — verified live: the identical request via
    # curl_cffi impersonating Chrome + the session's cf_clearance cookie returns
    # the real CSV/PDF (200). So impersonate, which the whole provider then uses.
    uses_tls_impersonation = True

    def __init__(self, *, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        #: Stashed by `prepare_session` so a 401 on a still-unexpired JWT can
        #: remint in-band. The singleton is leader-locked per sweep, so this is
        #: one-caller-at-a-time, not shared mutable state across requests.
        self._db: AsyncSession | None = None
        self._remint_attempted = False

    def build_headers(
        self, session: LoadedSession, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Cookie `token` plus `Authorization: Bearer` — the SPA sends both.

        `/api/session` and `/api/invoices` accept the JWT as the `token` cookie;
        some routes only accept the Bearer. Sending both matches the browser.
        """
        headers = super().build_headers(session, extra)
        token = (session.tokens or {}).get("access_token") or (
            session.cookies or {}
        ).get("token")
        if token and not any(k.lower() == "authorization" for k in headers):
            headers["Authorization"] = f"Bearer {token}"
        org = self._org_id(session)
        headers.setdefault("X-Roo-Org-Id", org)
        headers.setdefault("X-Hub-Api-Caller", "partner-hub")
        headers.setdefault("Origin", _HUB)
        headers.setdefault("Referer", f"{_HUB}/analytics?orgId={org}")
        headers.setdefault("Accept", "application/json, text/plain, */*")
        return headers

    def _org_id(self, session: LoadedSession) -> str:
        """The `orgId` / `org_id` every call is scoped to.

        Resolved by `_augment_from_db` before the sweep, so by the time this runs
        it is present. If it is genuinely missing (no session token, no account
        `extras.org_id`) the channel cannot be scoped — raise rather than guess a
        stale default that returns a wrong-scope 401.
        """
        for source in (session.tokens or {}, session.header_profile or {}):
            value = _first(
                source, "org_id", "orgId", "organisation_id", "organization_id"
            )
            if value:
                return str(value)
        raise AggregatorUnavailableError(
            "deliveroo: no org_id (set aggregator_account.extras.org_id)"
        )

    def _restaurant_ids(self, session: LoadedSession) -> list[str]:
        tokens = session.tokens or {}
        raw = tokens.get("restaurant_ids") or tokens.get("restaurantIds")
        if isinstance(raw, list) and raw:
            return [str(x) for x in raw if x]
        if isinstance(raw, str) and raw.strip():
            return [part.strip() for part in raw.split(",") if part.strip()]
        extras = tokens.get("restaurants")
        if isinstance(extras, list) and extras:
            return [str(x) for x in extras if x]
        raise AggregatorUnavailableError(
            "deliveroo: no outlet ids (seed aggregator_branch_map for deliveroo)"
        )

    async def get_opening_hours(self, session: LoadedSession, outlet_id: str) -> Any:
        """One outlet's weekly opening hours, read server-side (no headed worker).

        `GET /api/restaurants/{id}/opening_hours` -> `{hours:[{day_of_week,
        local_start_time, local_end_time}]}`. The Partner Hub SPA stopped *firing*
        this on page load (it was restructured onto an /api-gw/ gateway, 2026-09),
        so the passive page-capture returned nothing — but the endpoint itself is
        live and replays fine over the TLS-impersonating transport with the session's
        cf_clearance (verified on the VM: 200 for all three MM outlets). So hours are
        a direct read like Careem/Noon, not a worker push. The menu still needs the
        headed worker: its webrom host wants a `logon-pass` token the SPA mints with
        context this server call cannot reproduce (`403 Invalid access check`)."""
        return await self.request_json(
            session, "GET", f"{_API}/restaurants/{outlet_id}/opening_hours"
        )

    async def put_opening_hours(
        self, session: LoadedSession, outlet_id: str, hours: list[dict[str, Any]]
    ) -> Any:
        """Write one outlet's weekly opening hours (same shape the GET returns).

        `PUT /api/restaurants/{id}/opening_hours` with `{hours:[...]}`. Dry-run
        writers never reach here; a live write is behind `CATALOG_SYNC_ENABLED`.
        """
        return await self.request_json(
            session,
            "PUT",
            f"{_API}/restaurants/{outlet_id}/opening_hours",
            json_body={"hours": hours},
        )

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
        """One authenticated JSON call, reminting in-band on a stale-but-unexpired JWT.

        `_token_is_fresh` trusts the JWT `exp`. Marketplace-invalidated tokens
        still have a future exp, so the first data call 401s. On that
        `AggregatorAuthError`, mint via `_login` once (never `/api/session/refresh`,
        which persists a dead token) and retry the same request. A second 401
        propagates so ingest flips `needs_bootstrap`.
        """
        try:
            return await super().request_json(
                session,
                method,
                url,
                headers=headers,
                params=params,
                json_body=json_body,
                data=data,
            )
        except AggregatorAuthError:
            reminted = await self._remint_after_stale_token(session)
            if reminted is None:
                raise
            return await super().request_json(
                reminted,
                method,
                url,
                headers=headers,
                params=params,
                json_body=json_body,
                data=data,
            )

    async def _remint_after_stale_token(
        self, session: LoadedSession
    ) -> LoadedSession | None:
        """Full `_login` once after a 401 on a token `_token_is_fresh` still trusted.

        Overlays the minted cookies/tokens onto `session` so later calls in the
        same sweep (which hold the original object) send the new Bearer.
        """
        if self._remint_attempted or self._db is None:
            return None
        self._remint_attempted = True
        logger.info(
            "deliveroo: 401 with a still-unexpired token; reminting via _login "
            "(not /api/session/refresh)"
        )
        fresh = await self._login(self._db, session)
        if fresh is None:
            return None
        fresh = await self._augment_from_db(self._db, fresh)
        if fresh is None:
            return None
        session.cookies = dict(fresh.cookies or {})
        session.tokens = dict(fresh.tokens or {})
        session.header_profile = dict(fresh.header_profile or {})
        session.token_expires_at = fresh.token_expires_at
        session.cookie_expires_at = fresh.cookie_expires_at
        session.status = fresh.status
        return session

    def _token_is_fresh(self, session: LoadedSession) -> bool:
        exp = session.token_expires_at
        if exp is None:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp > utcnow() + _REFRESH_SKEW

    async def prepare_session(
        self, db: AsyncSession, session: LoadedSession | None
    ) -> LoadedSession | None:
        """Mint or refresh a Partner Hub JWT from `aggregator_account`.

        Called by ingest before a sweep so Deliveroo does not depend on a
        headed Chrome capture. Email/password only — no OTP on this channel.
        The prepared session is then augmented with the org and outlet ids from
        the DB (account `extras` + `aggregator_branch_map`) so the sweep never
        relies on the stale hard-coded fallbacks.
        """
        self._db = db
        self._remint_attempted = False
        prepared = await self._resolve_session(db, session)
        return await self._augment_from_db(db, prepared)

    async def _resolve_session(
        self, db: AsyncSession, session: LoadedSession | None
    ) -> LoadedSession | None:
        if (
            session is not None
            and session.status == SESSION_LIVE
            and self._token_is_fresh(session)
        ):
            return session
        # A stale token: mint a fresh one with the stored email/password. We do NOT
        # use `/api/session/refresh` here: verified live 2026-09-02, it returns a token
        # with a fresh `exp` that the data endpoints still 401, and worse it persists
        # that dead token so the next load reads it as "fresh" and every call 401s.
        # A full `_login` is proven to authenticate (200 on the same request), there is
        # no OTP on this channel, and it runs at most once per ~hour token life, so it
        # is both cheaper in failures and simpler than validating a refresh result.
        return await self._login(db, session)

    async def _augment_from_db(
        self, db: AsyncSession, session: LoadedSession | None
    ) -> LoadedSession | None:
        """Fill the session's `org_id` / `restaurant_ids` from the DB when the
        session itself does not carry them — so a Chrome-captured session (which
        has neither) resolves outlets from `aggregator_branch_map` and the org
        from the account, not from the stale module constants."""
        if session is None:
            return None
        tokens = dict(session.tokens or {})
        if not _first(tokens, "org_id", "orgId", "organisation_id", "organization_id"):
            org = await self._org_from_account(db)
            if not org:
                raise AggregatorUnavailableError(
                    "deliveroo: no org_id in session or account extras "
                    "(set aggregator_account.extras.org_id)"
                )
            tokens["org_id"] = org
        if not _outlet_ids_in(tokens):
            outlets = await _outlet_ids_from_map(db)
            if not outlets:
                raise AggregatorUnavailableError(
                    "deliveroo: no outlet ids in session or aggregator_branch_map "
                    "(seed the deliveroo branch map)"
                )
            tokens["restaurant_ids"] = outlets
        session.tokens = tokens
        return session

    async def _org_from_account(self, db: AsyncSession) -> str | None:
        from app.services.aggregators import account_store

        account = await account_store.load(db, self.channel)
        if account is not None and account.extras:
            value = _first(account.extras, "org_id", "orgId")
            if value:
                return str(value)
        return None

    async def _login(
        self, db: AsyncSession, previous: LoadedSession | None
    ) -> LoadedSession | None:
        from app.services.aggregators import account_store, session_store

        account = await account_store.load(db, self.channel)
        if account is None or not account.email or not account.password:
            # No credentials to mint with — the httpx path can't help and neither
            # can the worker (same account). Return None so the sweep flags the
            # channel needs_bootstrap (visible) rather than proceeding on a stale
            # token that will 401 anyway.
            logger.warning("deliveroo: no stored email/password; cannot HTTP-login")
            return None
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout, http2=True) as client:
            response = await client.post(
                _LOGIN_URL,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "application/json",
                    "Accept-Language": "en-GB,en;q=0.9,ar;q=0.8",
                    "Origin": _HUB,
                    "Referer": f"{_HUB}/login",
                    "Content-Type": "application/json",
                },
                json={"email": account.email, "password": account.password},
            )
        if response.status_code >= 400:
            # A hard login failure is a dead credential, not a transient blip.
            # Returning the *previous* (stale) session here is exactly what made
            # the sweep 401 forever: it proceeded on an expired Bearer. Return None
            # so the reauth path takes over and the channel is flagged, visible.
            logger.warning("deliveroo login returned %s", response.status_code)
            return None
        body = response.json()
        token = body.get("access_token") or ""
        if not token:
            logger.warning("deliveroo login returned no access_token")
            return None
        # Org id: the ACCOUNT's org (extras.org_id) is authoritative, then the
        # login body. Do NOT overwrite it with restaurant_companies[0].id — that is
        # the restaurant *company* id, a different entity, and scoping every request
        # to it returned a 401 one second after a "successful" login (the autonomous
        # symptom). `_augment_from_db` backfills from the account if both are absent.
        org_id = str(
            (account.extras or {}).get("org_id")
            or _first(body, "org_id", "orgId")
            or ""
        )
        # Outlets from the login body, else the branch map. No hard-coded fallback —
        # `_augment_from_db` raises loudly if neither yields ids.
        restaurant_ids = _restaurant_ids_from_login(body) or await _outlet_ids_from_map(
            db
        )
        exp = _jwt_exp(token)
        header_profile = dict(previous.header_profile or {}) if previous else {}
        header_profile.setdefault("user-agent", _BROWSER_UA)
        header_profile.setdefault("accept-language", "en-GB,en;q=0.9,ar;q=0.8")
        # Preserve the browser-captured anti-bot cookies (cf_clearance et al.) from
        # the prior session — the mint returns only the JWT, and dropping the rest
        # left the Cloudflare-fronted data endpoint without the cookie it needs, so
        # the minted Bearer 401'd. Carry them forward; overlay the fresh token.
        cookies = dict((previous.cookies if previous else None) or {})
        cookies["token"] = token
        tokens = {
            "access_token": token,
            "session_id": body.get("session_id"),
            "restaurant_ids": restaurant_ids,
        }
        if org_id:
            tokens["org_id"] = org_id
        await session_store.upsert_bootstrap(
            db,
            channel=self.channel,
            cookies=cookies,
            tokens=tokens,
            header_profile=header_profile,
            token_expires_at=exp,
            cookie_expires_at=exp,
        )
        logger.info(
            "deliveroo HTTP login ok; org=%s, %s outlet(s), carried cookies=%s, "
            "token exp %s",
            org_id or "(from account)",
            len(restaurant_ids),
            sorted(cookies.keys()),
            exp.isoformat() if exp else "unknown",
        )
        return await session_store.load(db, self.channel)

    async def _refresh(
        self, db: AsyncSession, session: LoadedSession
    ) -> LoadedSession | None:
        from app.services.aggregators import session_store

        try:
            data = await self.request_json(session, "POST", _REFRESH_URL, json_body={})
        except Exception:  # noqa: BLE001 — fall through to a full login
            logger.info("deliveroo token refresh failed; will re-login")
            return None
        token = ""
        if isinstance(data, dict):
            token = str(data.get("access_token") or data.get("accessToken") or "")
        if not token:
            return None
        exp = _jwt_exp(token)
        tokens = dict(session.tokens or {})
        tokens["access_token"] = token
        cookies = dict(session.cookies or {})
        cookies["token"] = token
        await session_store.upsert_bootstrap(
            db,
            channel=self.channel,
            cookies=cookies,
            tokens=tokens,
            header_profile=dict(session.header_profile or {}),
            token_expires_at=exp,
            cookie_expires_at=exp,
        )
        return await session_store.load(db, self.channel)

    # ── sales ────────────────────────────────────────────────────────────────
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        start = since.date().isoformat()
        # Deliveroo's `end_date` is EXCLUSIVE (an end of the 27th returns up to the
        # 26th), while `until` here is the LAST day to include — so advance it a day.
        # Without this a single-day "yesterday" window (start == until.date()) is an
        # empty range and the outlet returns no orders (verified: the 27th's order
        # was silently dropped until end_date became the 28th).
        end = (until.date() + timedelta(days=1)).isoformat()
        orders: list[StandardOrder] = []
        gaps: list[str] = []
        for restaurant_id in self._restaurant_ids(session):
            listing = await self.request_json(
                session,
                "GET",
                f"{_API}/restaurants/{restaurant_id}/orders",
                params={"start_date": start, "end_date": end},
            )
            rows = _as_list(listing, "orders", "data")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                order_id = str(row.get("order_id") or row.get("id") or "")
                parsed = self._parse_list_order(row, restaurant_id)
                if parsed is None:
                    continue
                if order_id:
                    try:
                        detail = await self.request_json(
                            session, "GET", f"{_API}/orders/{order_id}"
                        )
                    except AggregatorAuthError:
                        # A session that dies mid-detail is dead for the whole
                        # sweep — let it propagate so ingest flips the session to
                        # needs_bootstrap instead of silently dropping items and
                        # recording every remaining order as a per-order "gap".
                        raise
                    except AggregatorUnavailableError:  # keep the list row
                        detail = None
                        gaps.append(order_id)
                    if isinstance(detail, dict):
                        parsed = self._merge_order_detail(parsed, detail, restaurant_id)
                orders.append(parsed)
        return SalesResult(
            orders=orders,
            truncation_note=(
                "Deliveroo order detail missing for: " + ", ".join(gaps)
                if gaps
                else None
            ),
        )

    def _parse_list_order(
        self, row: dict[str, Any], restaurant_id: str
    ) -> StandardOrder | None:
        order_id = str(row.get("order_id") or row.get("id") or "").strip()
        order_number = str(row.get("order_number") or "").strip()
        external_id = order_id or order_number
        if not external_id:
            return None
        timeline = row.get("timeline") if isinstance(row.get("timeline"), dict) else {}
        placed_at = _parse_dt(
            timeline.get("placed_at") if timeline else None
        ) or _parse_dt(row.get("placed_at"))
        business_date = None
        if placed_at is not None:
            local = (
                placed_at.astimezone(_BUSINESS_TZ) if placed_at.tzinfo else placed_at
            )
            business_date = local.date().isoformat()
        gross = _fils(row.get("amount"))
        status = (row.get("status") or "").strip() or None
        return StandardOrder(
            external_order_id=external_id,
            # The short human order number (`5254`) is the shared key with the
            # GrubOps/Foodics order on a GrubOps branch — Foodics stores it as the
            # order's `external_id`. Our own `external_order_id` is Deliveroo's
            # UUID, which Foodics never sees, so WITHOUT this the promotion could
            # not converge onto the GrubOps order and filed a standalone duplicate
            # on Barsha/Sharjah (Deliveroo's `display_ref` was NULL). Setting it
            # lets `_find_mm_order` match `display_ref` → `grubops_order_map.external_id`.
            display_ref=order_number or None,
            external_outlet_id=restaurant_id,
            business_date=business_date,
            placed_at=placed_at,
            status=status,
            currency="AED",
            gross_sales=gross,
            net_sales=gross,
            raw=dict(row),
        )

    def _merge_order_detail(
        self, order: StandardOrder, detail: dict[str, Any], restaurant_id: str
    ) -> StandardOrder:
        # Lines come from the per-order detail (`GET /api/orders/{order_id}`):
        # `detail.items = [{name, quantity, modifiers, unit_price:{fractional},
        # total_price:{fractional}, category_name}]`. A cancelled order still
        # lists its items here, so every order with a detail keeps its lines;
        # only an order whose detail lookup was gated (detail is None) has none.
        items: list[StandardOrderItem] = []
        for index, item in enumerate(_as_list(detail, "items", "order_items"), start=1):
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or item.get("item_name") or "").strip()
            category = (item.get("category_name") or item.get("category") or "").strip()
            qty = item.get("quantity")
            unit = _fils(item.get("unit_price") or item.get("price"))
            total = _fils(
                item.get("total_price") or item.get("total") or item.get("amount")
            )
            raw_mods = _raw_modifiers(item)
            parsed_mods = expand_modifiers(raw_mods) if raw_mods else []
            mods_text: str | None = None
            if raw_mods:
                try:
                    mods_text = json.dumps(raw_mods)
                except (TypeError, ValueError):
                    mods_text = str(raw_mods)
            items.append(
                StandardOrderItem(
                    source_key=f"{order.external_order_id}:{index}",
                    grain=GRAIN_LINE,
                    item_name=name or None,
                    category_name=category or None,
                    quantity=Decimal(str(qty)) if qty is not None else None,
                    unit_price=unit,
                    gross_sales=total,
                    net_sales=total,
                    amount_is_known=total is not None,
                    modifiers=parsed_mods,
                    modifiers_text=mods_text,
                    business_date=order.business_date,
                )
            )

        # Timeline — accepted / delivered / cancelled timestamps.
        detail_timeline = detail.get("timeline")
        if not isinstance(detail_timeline, dict):
            detail_timeline = {}
        accepted_at = _parse_dt(
            _first(
                detail_timeline,
                "accepted_at",
                "acceptedAt",
                "confirmed_at",
                "confirmedAt",
            )
        )
        delivered_at = _parse_dt(
            _first(
                detail_timeline,
                "delivered_at",
                "deliveredAt",
                "completed_at",
                "completedAt",
            )
        )
        cancelled_at = _parse_dt(
            _first(
                detail_timeline,
                "cancelled_at",
                "cancelledAt",
                "cancellation_at",
                "cancellationAt",
            )
        )

        # Status history — the full lifecycle from `detail.timeline`, one tz-aware
        # event per step whose timestamp is present (see `_TIMELINE_STEPS`), plus
        # the terminal delivered/cancelled step timed by its own timestamp. ISO
        # values here are tz-aware (+04:00); `_parse_dt` keeps them aware.
        status_events: list[StandardStatusEvent] = []
        for sequence, (key, word) in enumerate(_TIMELINE_STEPS):
            at = _parse_dt(detail_timeline.get(key))
            if at is None:
                continue
            status_events.append(
                StandardStatusEvent(status=word, at=at, sequence=sequence)
            )
        terminal_word = str(detail.get("status") or order.status or "").strip().lower()
        terminal_at = delivered_at or cancelled_at
        if terminal_word in _TERMINAL_STATUSES and terminal_at is not None:
            status_events.append(
                StandardStatusEvent(
                    status=terminal_word,
                    at=terminal_at,
                    sequence=len(_TIMELINE_STEPS),
                )
            )

        # Customer — name and phone when the detail exposes them.
        customer = detail.get("customer")
        if not isinstance(customer, dict):
            customer = {}
        customer_name = (
            str(
                _first(customer, "name", "full_name", "fullName")
                or _first(detail, "customer_name", "customerName")
                or ""
            ).strip()
            or None
        )
        # Deliveroo withholds customer name/phone from the Partner Hub — the order
        # detail exposes only a stable numeric consumer id ({"customer": {"id": N}}).
        # That id is not PII, but it IS consistent across a consumer's orders, so
        # storing it as a pseudonymous name lets the shop see repeat customers even
        # though it can never see who they are. A real name (should Deliveroo ever
        # start returning one) always wins over the id.
        if customer_name is None:
            customer_id = _first(
                customer, "id", "customer_id", "customerId", "consumer_id", "consumerId"
            ) or _first(
                detail, "customer_id", "customerId", "consumer_id", "consumerId"
            )
            if customer_id is not None:
                customer_name = f"Deliveroo customer {customer_id}"
        customer_phone = (
            str(
                _first(customer, "phone", "phone_number", "phoneNumber", "mobile")
                or _first(detail, "customer_phone", "customerPhone", "customer_mobile")
                or ""
            ).strip()
            or None
        )

        raw = dict(order.raw or {})
        raw["detail"] = detail
        return StandardOrder(
            external_order_id=order.external_order_id,
            display_ref=order.display_ref,  # carry the short order number (GrubOps convergence key)
            external_outlet_id=order.external_outlet_id or restaurant_id,
            business_date=order.business_date,
            placed_at=order.placed_at,
            accepted_at=accepted_at or order.accepted_at,
            delivered_at=delivered_at or order.delivered_at,
            cancelled_at=cancelled_at or order.cancelled_at,
            status=order.status or (detail.get("status") or None),
            currency=order.currency,
            customer_name=customer_name or order.customer_name,
            customer_phone=customer_phone or order.customer_phone,
            status_events=status_events or order.status_events,
            gross_sales=order.gross_sales
            or _fils(detail.get("amount") or detail.get("total")),
            net_sales=order.net_sales
            or _fils(detail.get("amount") or detail.get("total")),
            commission_amount=order.commission_amount,
            vat_amount=order.vat_amount,
            net_payable=order.net_payable,
            items=items or order.items,
            raw=raw,
        )

    # ── finance (statements from invoices; payouts only when distinct) ───────
    async def fetch_statements(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> StatementsResult:
        org_id = self._org_id(session)
        from_date, to_date = since.date(), until.date()
        statements: list[StandardStatement] = []
        gaps: list[str] = []
        for invoice in await self._list_invoices(session, org_id):
            period_start = _parse_date(
                _first(invoice, "period_start", "start_date", "from", "billing_start")
            )
            period_end = _parse_date(
                _first(invoice, "period_end", "end_date", "to", "billing_end")
            )
            if period_end and period_end < from_date:
                continue
            if period_start and period_start > to_date:
                continue
            invoice_id = _first(invoice, "id", "invoice_id", "reference", "number")
            if invoice_id is None:
                continue
            statement_id = str(invoice_id)
            due_date = _parse_date(
                _first(
                    invoice,
                    "payment_due_date",
                    "due_date",
                    "due_at",
                    "paid_at",
                    "pay_date",
                )
            )
            net_payable = _fils(invoice.get("total")) or _num(
                _first(invoice, "net_payable", "amount", "amount_due")
            )
            currency = _first(invoice, "currency", "currency_code") or "AED"

            lines: list[StandardStatementLine] = []
            csv_text, csv_bytes = await self._invoice_csv(session, org_id, statement_id)
            if csv_text is None:
                gaps.append(statement_id)
            else:
                lines = self._statement_lines(statement_id, csv_text)

            invoice_archive = await self._archive_invoice(
                session, org_id, statement_id, csv_bytes
            )

            statements.append(
                StandardStatement(
                    statement_id=statement_id,
                    period_start=_iso(period_start),
                    period_end=_iso(period_end),
                    payment_due_date=_iso(due_date),
                    net_payable=net_payable,
                    currency=currency,
                    lines=lines,
                    invoice_object_key=(
                        invoice_archive.object_key if invoice_archive else None
                    ),
                    invoice_content_type=(
                        invoice_archive.content_type if invoice_archive else None
                    ),
                    invoice_original_filename=(
                        invoice_archive.original_filename if invoice_archive else None
                    ),
                    invoice_fetched_at=(
                        invoice_archive.fetched_at if invoice_archive else None
                    ),
                    invoice_attachments=(
                        invoice_archive.attachments if invoice_archive else None
                    ),
                    raw=invoice if isinstance(invoice, dict) else None,
                )
            )
        return StatementsResult(
            statements=statements,
            truncation_note=(
                "Deliveroo statement/invoice downloads blocked (Cloudflare "
                "challenge or IP-bound cf_clearance) for invoices: "
                + ", ".join(gaps)
                + " — per-order lines and VAT PDFs need an in-page/clearance "
                "capture path, not httpx from the ingest host."
                if gaps
                else None
            ),
        )

    async def _archive_invoice(
        self,
        session: LoadedSession,
        org_id: str,
        statement_id: str,
        csv_bytes: bytes | None,
    ) -> StoredStatementInvoice | None:
        """Persist the settlement VAT document to private R2, best-effort.

        Prefers the statement PDF (the document finance claims VAT from); falls
        back to the statement CSV bytes we already fetched. Returns None when
        every download is gated (Cloudflare) or R2 is unconfigured — the finance
        sweep still records the statement summary. Never raises: an archive
        failure must not fail the sweep.
        """
        # `pdf` / `invoice_pdf` are the real file_types (the invoice's own
        # download_links use them); `statement_pdf` 500s "failed to get download url".
        pdf_bytes = await self._invoice_file(session, statement_id, "pdf")
        if pdf_bytes is None:
            pdf_bytes = await self._invoice_file(session, statement_id, "invoice_pdf")
        try:
            if pdf_bytes:
                return store_statement_invoice(
                    channel=self.channel,
                    statement_id=statement_id,
                    filename=f"{statement_id}.pdf",
                    body=pdf_bytes,
                    content_type="application/pdf",
                    extra_files=(
                        [(f"{statement_id}.csv", csv_bytes, "text/csv")]
                        if csv_bytes
                        else None
                    ),
                )
            if csv_bytes:
                return store_statement_invoice(
                    channel=self.channel,
                    statement_id=statement_id,
                    filename=f"{statement_id}.csv",
                    body=csv_bytes,
                    content_type="text/csv",
                )
        except Exception:  # noqa: BLE001 — archival is best-effort
            logger.exception("deliveroo invoice archive failed for %s", statement_id)
        return None

    async def _invoice_file(
        self, session: LoadedSession, invoice_id: str, file_type: str
    ) -> bytes | None:
        """Raw bytes of one invoice file_type, or None on a gate/HTML/non-200."""
        try:
            response = await self.request_raw(
                session,
                "GET",
                f"{_API}/invoices/{invoice_id}/download",
                params={
                    "file_type": file_type,
                    "invoice_origin": "restaurant-payments",
                },
            )
        except AggregatorUnavailableError:
            return None
        status = getattr(response, "status_code", 0)
        ctype = ""
        resp_headers = getattr(response, "headers", None)
        if resp_headers is not None:
            ctype = str(resp_headers.get("content-type") or "")
        raw_content = getattr(response, "content", None)
        if (
            status != 200
            or "text/html" in ctype
            or not isinstance(raw_content, (bytes, bytearray))
        ):
            return None
        body = bytes(raw_content)
        # A Cloudflare interstitial can come back 200 with an HTML body.
        if body[:15].lstrip().startswith(b"<!"):
            return None
        return body

    async def fetch_payouts(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> PayoutsResult:
        """One derived payout per invoice — Deliveroo settles each 1:1.

        Partner Hub exposes settlement as invoices, not a separate bank-transfer
        feed: each invoice IS the payment (its `total` is transferred on its
        `due_at`). So a payout is derived per invoice, keyed on the same id as the
        statement (`transfer_id = statement_id`) and marked `transfer_status
        = "derived"` so it is never mistaken for a bank-feed row — this is what
        lets the statement↔payout back-link (`link_statements_to_payouts`) close
        the payments leg for Deliveroo like the other channels, and it is honest
        because for Deliveroo the invoice and the transfer are the same event.
        """
        org_id = self._org_id(session)
        from_date, to_date = since.date(), until.date()
        payouts: list[StandardPayout] = []
        for invoice in await self._list_invoices(session, org_id):
            period_start = _parse_date(
                _first(invoice, "period_start", "start_date", "from", "billing_start")
            )
            period_end = _parse_date(
                _first(invoice, "period_end", "end_date", "to", "billing_end")
            )
            if period_end and period_end < from_date:
                continue
            if period_start and period_start > to_date:
                continue
            invoice_id = _first(invoice, "id", "invoice_id", "reference", "number")
            if invoice_id is None:
                continue
            statement_id = str(invoice_id)
            due_date = _parse_date(
                _first(invoice, "payment_due_date", "due_date", "due_at", "paid_at")
            )
            payouts.append(
                StandardPayout(
                    transfer_id=statement_id,
                    statement_id=statement_id,
                    transfer_date=_iso(due_date),
                    payment_due_date=_iso(due_date),
                    transfer_amount=_fils(invoice.get("total"))
                    or _num(_first(invoice, "net_payable", "amount", "amount_due")),
                    transfer_status="derived",
                    payment_reference=str(
                        _first(invoice, "reference", "number") or statement_id
                    ),
                    currency=_first(invoice, "currency", "currency_code") or "AED",
                )
            )
        return PayoutsResult(payouts=payouts)

    async def _list_invoices(
        self, session: LoadedSession, org_id: str
    ) -> list[dict[str, Any]]:
        listing = await self.request_json(
            session, "GET", f"{_API}/invoices", params={"org_id": org_id}
        )
        return [
            row
            for row in _as_list(listing, "invoices", "data")
            if isinstance(row, dict)
        ]

    async def _invoice_csv(
        self, session: LoadedSession, org_id: str, invoice_id: str
    ) -> tuple[str | None, bytes | None]:
        """Download a statement CSV, returning `(text, raw_bytes)`.

        Returns `(None, None)` on a 403 / HTML gate-page — callers treat that as
        a missing statement rather than a hard error. `raw_bytes` is the verbatim
        response body used for archival; the text is the decoded CSV for parsing.
        """
        response = await self.request_raw(
            session,
            "GET",
            f"{_API}/invoices/{invoice_id}/download",
            params={
                # `csv`, not `statement_csv` — the real file_type the invoice's own
                # download_links use ("failed to get download url" otherwise).
                "file_type": "csv",
                "invoice_origin": "restaurant-payments",
            },
        )
        status = getattr(response, "status_code", 0)
        ctype = ""
        resp_headers = getattr(response, "headers", None)
        if resp_headers is not None:
            ctype = str(resp_headers.get("content-type") or "")
        raw_content = getattr(response, "content", None)
        if isinstance(raw_content, (bytes, bytearray)):
            raw_bytes: bytes | None = bytes(raw_content)
            text = raw_bytes.decode("utf-8-sig", errors="replace")
        else:
            raw_bytes = None
            text = getattr(response, "text", None) or ""
        if status != 200 or "text/html" in ctype or text.lstrip().startswith("<!"):
            return None, None
        return text, (raw_bytes if raw_bytes else text.encode())

    def _statement_lines(
        self, statement_id: str, text: str
    ) -> list[StandardStatementLine]:
        """The per-order settlement lines behind one invoice.

        Ported from `normalize_deliveroo_statement_lines`: the CSV has a preamble,
        so the header row is found by its `Restaurant Name` / `Order ID` shape,
        and each data row emits up to five non-zero lines (order value, an
        activity adjustment, commission, its VAT, and the net payable).

        Live Partner Hub CSVs carry both `Order Number` (a long numeric, e.g.
        ``51135384652``) and `Order ID` (a v4 UUID). `Order ID` is the sales
        detail `drn_id` — not list `order_id` (a different v3 UUID) and not the
        short `order_number` / `display_ref` (e.g. ``9170``). Line
        `external_order_id` is therefore the CSV `Order ID`.
        """
        reader = csv.reader(io.StringIO(text))
        headers: list[str] | None = None
        lines: list[StandardStatementLine] = []
        row_number = 0
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) == 1:
                continue
            if row[0].strip() == "Restaurant Name" and "Order ID" in row:
                headers = [cell.strip() for cell in row]
                continue
            if not headers:
                continue
            row_number += 1
            data = dict(zip(headers, row, strict=False))
            external_order_id = (data.get("Order ID") or "").strip() or None
            activity = (data.get("Activity") or "").strip()
            note = (data.get("Note") or "").strip() or None
            line_date = _iso(
                dt.date()
                if (dt := _utc_to_business(data.get("Delivery Date & Time (UTC)")))
                else None
            )
            base_key = external_order_id or f"{statement_id}:{row_number}"
            values = (
                ("gross_sales", "gross_sales", _num(data.get("Order Value (د.إ)"))),
                (
                    "adjustment",
                    activity.lower().replace(" ", "_") or "adjustment",
                    _num(data.get("Adjustment Net (د.إ)")),
                ),
                ("fee", "commission", _num(data.get("Deliveroo Commission (د.إ)"))),
                (
                    "vat",
                    "commission_vat",
                    _num(data.get("Commission / Adjustment VAT (د.إ)")),
                ),
                ("net_payable", "net_payable", _num(data.get("Total Payable"))),
            )
            for line_type, fee_category, amount in values:
                if amount is None or amount == 0:
                    continue
                lines.append(
                    StandardStatementLine(
                        source_key=f"{statement_id}:{base_key}:{fee_category}",
                        statement_id=statement_id,
                        external_order_id=external_order_id,
                        line_date=line_date,
                        line_type=line_type,
                        fee_category=fee_category,
                        description=note or activity or fee_category,
                        amount=amount,
                        currency="AED",
                    )
                )
        return lines

    # ── in-page push path (invoice downloads fetched by the bootstrap worker) ──
    def parse_pushed_finance(self, payload: dict[str, Any]) -> StandardStatement | None:
        """Turn one in-page-fetched invoice payload into a StandardStatement.

        The invoice DOWNLOAD endpoint 403s over httpx behind Cloudflare, so the
        bootstrap worker fetches the statement CSV and PDF *in-page* (carrying
        the browser's `cf_clearance`) and pushes
        `{"invoice": <raw dict>, "statement_csv": <text|None>,
        "statement_pdf_b64": <b64|None>}`. This reconstructs the same
        `StandardStatement` `fetch_statements` builds — the invoice summary from
        the raw dict, per-order lines from the CSV via the shared
        `_statement_lines`, and the archived VAT PDF (with the CSV alongside) —
        so ingest stays thin. Returns None when the payload carries no keyable
        invoice.
        """
        invoice = payload.get("invoice")
        if not isinstance(invoice, dict):
            return None
        invoice_id = _first(invoice, "id", "invoice_id", "reference", "number")
        if invoice_id is None:
            return None
        statement_id = str(invoice_id)

        period_start = _parse_date(
            _first(invoice, "period_start", "start_date", "from", "billing_start")
        )
        period_end = _parse_date(
            _first(invoice, "period_end", "end_date", "to", "billing_end")
        )
        due_date = _parse_date(
            _first(
                invoice,
                "payment_due_date",
                "due_date",
                "due_at",
                "paid_at",
                "pay_date",
            )
        )
        net_payable = _fils(invoice.get("total")) or _num(
            _first(invoice, "net_payable", "amount", "amount_due")
        )
        currency = _first(invoice, "currency", "currency_code") or "AED"

        csv_text = payload.get("statement_csv")
        lines = (
            self._statement_lines(statement_id, csv_text)
            if isinstance(csv_text, str) and csv_text
            else []
        )
        csv_bytes = (
            csv_text.encode("utf-8") if isinstance(csv_text, str) and csv_text else None
        )

        pdf_bytes: bytes | None = None
        pdf_b64 = payload.get("statement_pdf_b64")
        if pdf_b64:
            try:
                pdf_bytes = base64.b64decode(pdf_b64)
            except Exception:  # noqa: BLE001 — a bad blob is "no PDF", not a crash
                logger.warning(
                    "deliveroo: invalid statement_pdf_b64 for %s", statement_id
                )

        invoice_archive = self._archive_pushed_invoice(
            statement_id, pdf_bytes, csv_bytes
        )

        return StandardStatement(
            statement_id=statement_id,
            period_start=_iso(period_start),
            period_end=_iso(period_end),
            payment_due_date=_iso(due_date),
            net_payable=net_payable,
            currency=currency,
            lines=lines,
            invoice_object_key=(
                invoice_archive.object_key if invoice_archive else None
            ),
            invoice_content_type=(
                invoice_archive.content_type if invoice_archive else None
            ),
            invoice_original_filename=(
                invoice_archive.original_filename if invoice_archive else None
            ),
            invoice_fetched_at=(
                invoice_archive.fetched_at if invoice_archive else None
            ),
            invoice_attachments=(
                invoice_archive.attachments if invoice_archive else None
            ),
            raw=invoice,
        )

    def _archive_pushed_invoice(
        self,
        statement_id: str,
        pdf_bytes: bytes | None,
        csv_bytes: bytes | None,
    ) -> StoredStatementInvoice | None:
        """Persist worker-fetched invoice bytes to private GCS, best-effort.

        Prefers the statement PDF (the VAT document) with the CSV alongside;
        falls back to the CSV alone. Never raises — an archive failure must not
        fail the ingest.
        """
        try:
            if pdf_bytes:
                return store_statement_invoice(
                    channel=self.channel,
                    statement_id=statement_id,
                    filename=f"{statement_id}.pdf",
                    body=pdf_bytes,
                    content_type="application/pdf",
                    extra_files=(
                        [(f"{statement_id}.csv", csv_bytes, "text/csv")]
                        if csv_bytes
                        else None
                    ),
                )
            if csv_bytes:
                return store_statement_invoice(
                    channel=self.channel,
                    statement_id=statement_id,
                    filename=f"{statement_id}.csv",
                    body=csv_bytes,
                    content_type="text/csv",
                )
        except Exception:  # noqa: BLE001 — archival is best-effort
            logger.exception(
                "deliveroo pushed invoice archive failed for %s", statement_id
            )
        return None


#: The module-level singleton, matching the careem/grubops/foodics providers —
#: stateless (the session is passed per call), so sharing it is free.
provider = DeliverooClient()
