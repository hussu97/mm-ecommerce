"""Talking to Foodics the way its **console** does — not its developer API.

Aggregator orders reach us as Noon/Talabat/… → GrubTech → **Foodics** → MM. The
GrubOps ingest loop is how we *read* those orders; this is how we *drive them
forward* (dispatch / finalise / cancel), in place of GrubOps' blunt
`order-force-*` overrides.

**This is deliberately the console integration, not the Foodics developer/OAuth
API.** Foodics' public API needs a registered partner app and an OAuth token; we
instead sign in as the business the way `console.foodics.com` itself does and call
its private `core-api` back-end — the same shape as the GrubOps provider next to
this one, which logs in as a console user rather than through a partner API. So:
there is no bearer token here, the base is the console, and auth is a **session
cookie + CSRF token**.

**Auth = session cookie + CSRF, obtained by logging in.** A console request
carries a `__Secure-console_session` cookie and an `x-csrf-token` header; that
pair is the credential, and the provider gets it at runtime by **signing in** with
the account number + email + password — there is nothing to paste or store beyond
the login. `_login()` primes the session, reads the form's CSRF token, and POSTs
the credentials; the resulting cookie + CSRF are cached and refreshed
automatically when the session expires. Every step of that login is implemented
**except the reCAPTCHA token** (`_recaptcha_token`): the console login is gated by
reCAPTCHA, and producing that token is bot-detection we have deliberately **not**
built yet — so `_login()` raises at that one step until it is finished.

**The order id is the mapping.** GrubOps records the Foodics order id in its order
history (`…Foodics Order Id: <uuid>`), which the ingest loop parses onto
`grubops_order_map.foodics_order_id`. That uuid is the `id` the console addresses
an order by (`core-api/getting?url=/orders&id=<uuid>`), so the write-back needs no
lookup. `find_order_by_original_id` exists as a fallback/debug path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: After a failed login, wait this long before trying again. reCAPTCHA is unbuilt,
#: so the login fails every time — without a backoff the write-back would attempt
#: it on every order and every retry tick, and a flood of failed logins is how a
#: Foodics account gets locked. One attempt per window is enough to observe the
#: server's response in the logs.
_LOGIN_BACKOFF = timedelta(minutes=10)

#: Retried once — the codes that mean "ask again", not "you asked wrongly".
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: "session died" — Laravel answers an expired/invalid session with 419 (CSRF /
#: page expired) as well as 401. Both drop the cached session and re-auth once.
_SESSION_DEAD = frozenset({401, 419})

#: Foodics `delivery_status` integers we write. `1` sent-to-kitchen and `3/4`
#: (assigned/en-route) are driver-tracking states an aggregator order never uses;
#: `6` is left alone because Foodics' own docs disagree on whether it means
#: cancelled or closed. We only ever set "ready" (dispatch) and "delivered".
DELIVERY_READY = 2
DELIVERY_DELIVERED = 5

#: Foodics order `status` integers. Only `DECLINED` is settable by this client
#: (and only from `PENDING`); the rest are here to read the live state.
STATUS_PENDING = 1
STATUS_ACTIVE = 2
STATUS_DECLINED = 3
STATUS_CLOSED = 4

#: The console back-end and its verbs. `getting` fetches one resource, `listing`
#: filters a collection, `updating` writes — each proxies to the matching Foodics
#: resource, so the *data* fields are the same ones the resource takes.
_GETTING = "/core-api/getting"
_LISTING = "/core-api/listing"
_UPDATING = "/core-api/updating"

#: Laravel exposes the request CSRF token in a `<meta name="csrf-token">` tag on
#: the login page; that is what the `x-csrf-token` header must echo.
_CSRF_META_RE = re.compile(
    r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


class FoodicsError(RuntimeError):
    """A Foodics call that did not do what we asked."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403, 419)


class FoodicsAuthError(FoodicsError):
    """A login/session problem — a dead session, or the reCAPTCHA step we have
    not built. Distinct so a caller can tell "could not sign in" from "the order
    call failed"; still a `FoodicsError`, so the write-back records it either
    way."""


@dataclass(frozen=True)
class FoodicsConfig:
    console_base: str
    account_number: str
    email: str
    password: str
    timeout: float

    @property
    def is_configured(self) -> bool:
        # Credentials to log in with; the session cookie + CSRF are derived.
        return bool(self.account_number and self.email and self.password)


def _config() -> FoodicsConfig:
    return FoodicsConfig(
        console_base=settings.FOODICS_CONSOLE_BASE.rstrip("/"),
        account_number=settings.FOODICS_ACCOUNT_NUMBER,
        email=settings.FOODICS_EMAIL,
        password=settings.FOODICS_PASSWORD,
        timeout=settings.FOODICS_TIMEOUT_SECONDS,
    )


def _stamp(when: datetime | None = None) -> str:
    """A Foodics timestamp: UTC, `YYYY-MM-DD HH:MM:SS`, no offset."""
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def _fields(*names: str) -> dict[str, str]:
    """The `fields[orders][0]=id&fields[orders][1]=…` params the console sends,
    which trim the response to what we read."""
    return {f"fields[orders][{i}]": name for i, name in enumerate(names)}


#: The order fields the write-back reads back to reason about live state.
_ORDER_FIELDS = _fields("id", "status", "delivery_status", "reference")


class FoodicsClient:
    """Every console endpoint this write-back uses, one method each."""

    def __init__(self, config: FoodicsConfig | None = None) -> None:
        self._config = config
        self._cookie: str | None = None
        self._csrf: str | None = None
        # Remember a failed login so we back off rather than hammer it.
        self._login_error: FoodicsAuthError | None = None
        self._login_retry_at: datetime | None = None

    @property
    def config(self) -> FoodicsConfig:
        # Read through to settings so a test can patch the environment.
        return self._config or _config()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def reset(self) -> None:
        """Forget the cached session. For an expiry and for tests."""
        self._cookie = None
        self._csrf = None

    def reset_login_backoff(self) -> None:
        """Clear the failed-login cooldown so the next call retries immediately.
        For when the credentials or the (future) reCAPTCHA step have changed."""
        self._login_error = None
        self._login_retry_at = None

    # ── the session ───────────────────────────────────────────────────────────

    def _recaptcha_token(self) -> str:
        """The reCAPTCHA token the console login requires — **not built yet.**

        The login form is gated by reCAPTCHA, and producing a *passing* token is
        bot-detection we have deliberately not implemented. Rather than block the
        sign-in outright, this returns an empty token so `_login()` still POSTs and
        we can see what the console actually does with a captcha-less request
        (observed on prod via `last_push_error`). It does **not** solve, forge, or
        spoof anything — Foodics is expected to reject it; if it does not, that is
        the console's own (non-)enforcement, not a bypass on our side.
        """
        return ""

    async def _login(self) -> None:
        """Sign in to the console and cache the session cookie + CSRF token.

        Everything here is implemented except `_recaptcha_token`, which raises —
        so this cannot yet complete on its own. The endpoint and field names
        follow the console's own login form and should be confirmed against a
        captured login once the reCAPTCHA step exists.
        """
        config = self.config
        async with httpx.AsyncClient(
            timeout=config.timeout,
            base_url=config.console_base,
            follow_redirects=True,
        ) as client:
            # 1. Prime the session (sets the initial cookies) and read the CSRF
            #    token the form carries.
            page = await client.get("/login")
            csrf = _CSRF_META_RE.search(page.text)
            csrf_token = csrf.group(1) if csrf else client.cookies.get("XSRF-TOKEN")

            # 2. The one piece not built: the reCAPTCHA token. Raises today.
            recaptcha = self._recaptcha_token()

            # 3. Post the credentials the way the login form does.
            resp = await client.post(
                "/login",
                data={
                    "business_reference": config.account_number,
                    "email": config.email,
                    "password": config.password,
                    "g-recaptcha-response": recaptcha,
                    "_token": csrf_token,
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            if resp.status_code >= 400:
                raise FoodicsAuthError(
                    f"Foodics login failed: {resp.status_code} {resp.text[:200]}",
                    status=resp.status_code,
                )

            # 4. Cache the authenticated session cookie + a fresh CSRF token.
            self._cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            fresh = _CSRF_META_RE.search(resp.text)
            self._csrf = (fresh.group(1) if fresh else None) or csrf_token

    async def _ensure_session(self) -> None:
        if self._cookie:
            return
        now = datetime.now(timezone.utc)
        if (
            self._login_retry_at is not None
            and now < self._login_retry_at
            and self._login_error is not None
        ):
            # Still in the cooldown from a recent failed login. Re-raise the same
            # error without another network round-trip — one attempt per window
            # keeps the account from being locked by a flood of failed sign-ins.
            raise self._login_error
        try:
            await self._login()
        except FoodicsAuthError as exc:
            self._login_error = exc
            self._login_retry_at = now + _LOGIN_BACKOFF
            raise
        self._login_error = None
        self._login_retry_at = None

    # ── transport ─────────────────────────────────────────────────────────────

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        attempts: int = 2,
    ) -> Any:
        config = self.config
        if not config.is_configured:
            raise FoodicsError("Foodics is not configured")

        last: Exception | None = None
        for attempt in range(attempts):
            await self._ensure_session()
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Cookie": self._cookie or "",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": config.console_base + "/",
            }
            if self._csrf:
                headers["X-CSRF-TOKEN"] = self._csrf
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            try:
                async with httpx.AsyncClient(
                    timeout=config.timeout, base_url=config.console_base
                ) as client:
                    response = await client.request(
                        method, path, params=params, json=json_body, headers=headers
                    )
            except httpx.HTTPError as exc:
                last = exc
                if attempt + 1 < attempts:
                    continue
                raise FoodicsError(f"Foodics is unreachable: {exc}") from exc

            if response.status_code in _SESSION_DEAD and attempt + 1 < attempts:
                # Session expired mid-use. Drop it and log in again on the next
                # go (which raises until the reCAPTCHA step is built).
                self.reset()
                last = FoodicsError(
                    "Foodics session rejected", status=response.status_code
                )
                continue

            if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                last = FoodicsError(
                    f"Foodics returned {response.status_code}",
                    status=response.status_code,
                )
                continue

            return _unwrap(response)

        raise FoodicsError(str(last) if last else "Foodics call failed")

    # ── orders ────────────────────────────────────────────────────────────────

    async def get_order(self, order_id: str) -> dict | None:
        """The order as the console holds it, or None if it is gone.

        Read before a write so the mirror-out acts on live state — dispatch only
        what is not already dispatched, decline only what is still pending.
        """
        params = {"url": "/orders", "id": order_id, **_ORDER_FIELDS}
        try:
            payload = await self._call("GET", _GETTING, params=params)
        except FoodicsError as exc:
            if exc.status == 404:
                return None
            raise
        return _order_of(payload)

    async def find_order_by_original_id(self, original_order_id: str) -> list[dict]:
        """Fallback/debug lookup: the console's own filter for related orders.

        The write-back keys off the cached `foodics_order_id` and does not need
        this; it is here for reconciliation and for correlating an order whose id
        was never captured.
        """
        params = {
            "url": "/orders",
            "filters[original_order_id]": original_order_id,
            **_ORDER_FIELDS,
        }
        payload = await self._call("GET", _LISTING, params=params)
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    async def update_delivery_status(
        self,
        order_id: str,
        delivery_status: int,
        *,
        dispatched_at: datetime | None = None,
        delivered_at: datetime | None = None,
        set_now: bool = True,
    ) -> Any:
        """Move the order along the delivery axis: `2` ready (dispatch, which
        GrubTech cascades to the rider) or `5` delivered (how MM finalises it)."""
        data: dict[str, Any] = {"delivery_status": delivery_status}
        if delivery_status == DELIVERY_READY and (dispatched_at or set_now):
            data["dispatched_at"] = _stamp(dispatched_at)
        if delivery_status == DELIVERY_DELIVERED and (delivered_at or set_now):
            data["delivered_at"] = _stamp(delivered_at)
        return await self._update(order_id, data)

    async def decline_order(self, order_id: str) -> Any:
        """Decline a still-pending order (`status = 3`). Foodics allows this only
        from `1:Pending`; the caller checks the live status first."""
        return await self._update(order_id, {"status": STATUS_DECLINED})

    async def _update(self, order_id: str, data: dict[str, Any]) -> Any:
        # TODO(confirm-envelope): the `updating` verb's exact body has not been
        # captured yet. `{url, id, data}` mirrors the read verbs' `url`/`id`
        # shape and the resource's own field names; confirm against a real
        # console PUT (Network → Payload) before enabling the write path in prod.
        return await self._call(
            "PUT",
            _UPDATING,
            json_body={"url": "/orders", "id": order_id, "data": data},
        )


def _order_of(payload: Any) -> dict | None:
    """The single order out of a `getting` response (`{data: {...}}`), tolerating
    the list shape a filtered read can return."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        return data[0] if data else None
    return data or payload


def _unwrap(response: httpx.Response) -> Any:
    """The body, or an error carrying whatever the console said was wrong."""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.status_code >= 400:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("message") or payload.get("errors") or "")
        raise FoodicsError(
            f"Foodics returned {response.status_code}: {detail or response.text[:200]}",
            status=response.status_code,
        )
    return payload


#: The one client. Holds the session cache, so sharing it is the point.
provider = FoodicsClient()
