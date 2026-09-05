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
the login. `_login()` primes the session, reads the form's CSRF token, asks
Google for the same reCAPTCHA v3 token the login page's own script would, and
POSTs the credentials the way Chrome in the UAE does (the shop's country, a
current Chrome UA and client hints — not python-httpx). The resulting cookie +
CSRF are cached and refreshed automatically when the session expires.

The form fields are the ones the live page posts (`business`, `email`,
`password`, `token`, `_token`). A 200 that is still `/login` is a rejection —
Laravel does not 401 a bad password, it re-renders the form — so success is
"we left the login page", not "the POST returned 200".

**The order id is the mapping.** GrubOps records the Foodics order id in its order
history (`…Foodics Order Id: <uuid>`), which the ingest loop parses onto
`grubops_order_map.foodics_order_id`. That uuid is the `id` the console addresses
an order by (`core-api/getting?url=/orders&id=<uuid>`), so the write-back needs no
lookup. `find_order_by_original_id` exists as a fallback/debug path.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: After a failed login, wait this long before trying again. A flood of failed
#: sign-ins is how a Foodics account gets locked; one attempt per window is
#: enough to observe the server's response in the logs.
_LOGIN_BACKOFF = timedelta(minutes=10)

#: Login talks to Foodics *and* Google for the reCAPTCHA token. The API-call
#: timeout is 8s, which is tight once Cloudflare has had a think; this is only
#: the sign-in round-trip.
_LOGIN_TIMEOUT = 20.0

#: Chrome 151 on Windows, reduced UA (patch frozen at 0.0.0). python-httpx's
#: default UA is how Cloudflare decides this is not a person at the console;
#: matching a current Chrome is what a regular shop login looks like.
_CHROME_MAJOR = "151"
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

#: UAE English first — the shop is in the UAE, the VM is in Doha, and a
#: US/HK Accept-Language on that hop is a country mismatch reCAPTCHA scores.
_ACCEPT_LANGUAGE = "en-AE,en;q=0.9,ar-AE;q=0.8,ar;q=0.7"

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

#: Foodics order `status` integers, from the console's own enum
#: (`PENDING:1 … VOID:7`). Captured 2026-08-25 from `useOrderActions`.
STATUS_PENDING = 1
STATUS_ACTIVE = 2
STATUS_DECLINED = 3
STATUS_CLOSED = 4  # Done — the Close Order button
STATUS_RETURNED = 5
STATUS_VOID = 7

#: Product-line statuses on the same console enum. Void sets lines to `VOID`;
#: a return (after close) uses `RETURNED` and is not something we write.
PRODUCT_VOID = 5

#: The console's Void Reason picker lists `reasons` with `type = 1`
#: (void/return). Prefer this name when the shop has it — it is on this
#: account — and fall back to the first undeleted type-1 reason otherwise.
_VOID_REASON_NAME = "Customer Cancelled"

#: The console back-end and its verbs. `getting` fetches one resource, `listing`
#: filters a collection, `updating` writes — each proxies to the matching Foodics
#: resource, so the *data* fields are the same ones the resource takes.
_GETTING = "/core-api/getting"
_LISTING = "/core-api/listing"
_SELECT_LISTING = "/core-api/select-listing"
_UPDATING = "/core-api/updating"
#: Create. Verified 2026-09-01 by reading the console's own API client, which
#: declares the verb→method map verbatim: `{name:'creating',method:'post'}` next
#: to `getting`(get)/`updating`(put)/`deleting`(delete). NOT `inserting` — that
#: string is absent from every bundle; `creating` is the create verb. Same
#: `{url, payload}` envelope as `_UPDATING`, so the resource is named in the
#: body, not the path.
_CREATING = "/core-api/creating"
#: Delete (`{name:'deleting',method:'delete'}` in the same verb map). Used to
#: remove a product from the Grubtech price tag — the surgical, reversible way to
#: take an item off the aggregator menu without deactivating it for the POS.
_DELETING = "/core-api/deleting"

#: The "Grubtech" price tag + menu group that define the aggregator menu for the
#: two Foodics-integrated branches. Account-stable ids (not secrets, not
#: environment-varying — the same Foodics account serves every deployment), so
#: they live here as constants rather than as five-place env vars. The price tag
#: is the authoritative aggregator product set + prices; the group is its
#: membership mirror. Confirmed live 2026-08-31. See
#: docs/integrators-and-aggregators.md.
FOODICS_GRUBTECH_PRICE_TAG_ID = "a056ee7e-5823-47af-9ab5-1029508c996b"
FOODICS_GRUBTECH_GROUP_ID = "a062ba1a-70b6-4bd7-8dac-f7986f33727f"

#: The Grubtech parent group's nine category subgroups — a new aggregator product
#: joins the one matching its MM category, and that subgroup already rolls into
#: Grubtech, so the product syncs to every marketplace. Read live from the group
#: record 2026-09-01 (`/core-api/getting?url=/groups&id=<Grubtech>` → `subgroups`).
#: Keyed by the subgroup name, which mirrors the MM category name.
FOODICS_GRUBTECH_SUBGROUPS: dict[str, str] = {
    "Cookie Melt": "a05d8155-d43b-469d-953b-aeaefed7b326",
    "Cakes": "a05d8176-5a8b-480f-862f-bfe40b4bc8d3",
    "Extras": "a05d8188-9809-4e1d-8314-cf647f867de4",
    "Cookies": "a063495f-292d-40af-9fcf-faca7a5d0c88",
    "Mix Boxes": "a0634a0f-96c9-4050-9e7e-65680d9c807b",
    "Brownies": "a0634a39-10f9-4c93-bdb8-00fb02767be4",
    "Eggless": "a0634a85-aa22-4fa8-98ca-377a202521e1",
    "Desserts": "a0634b49-3245-4749-8efd-73109b696769",
    "New In": "a1887db1-b3cc-48b6-a395-5de8ebf5b2b7",
}

#: The account's UAE VAT tax group and the product method codes every menu
#: product on this account carries. Read live from an existing Grubtech product
#: 2026-09-01 (`tax_group`, `pricing_method`, `selling_method`, `costing_method`).
#: A create must echo them or Foodics rejects the product.
FOODICS_VAT_TAX_GROUP_ID = "a03ed56a-9f3f-44ff-9c49-bfe3b25d55b9"
FOODICS_PRICING_METHOD = 1
FOODICS_SELLING_METHOD = 1
FOODICS_COSTING_METHOD = 2

#: Laravel exposes the request CSRF token in a `<meta name="csrf-token">` tag on
#: the login page; that is what the `x-csrf-token` header must echo. The form
#: also posts it as `_token` — prefer the form value when both are present.
_CSRF_META_RE = re.compile(
    r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_FORM_TOKEN_RE = re.compile(
    r'name=["\']_token["\']\s+value=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

#: reCAPTCHA v3 site key on the login page (`api.js?render=…` and
#: `recaptchClientID`). Confirmed 2026-08-25 against the live form.
_SITEKEY_RE = re.compile(
    r"recaptcha/api\.js\?render=([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_RECAPTCHA_VERSION_RE = re.compile(r"releases/([A-Za-z0-9_-]+)/")
_RECAPTCHA_ANCHOR_TOKEN_RE = re.compile(r'id="recaptcha-token"\s+value="([^"]+)"')
_RECAPTCHA_RRESP_RE = re.compile(r'\["rresp","([^"]+)"')


def _chrome_headers(
    *, navigate: bool = False, referer: str | None = None
) -> dict[str, str]:
    """UA, UAE language and client hints a current Chrome sends.

    Deliberately no `sec-fetch-*`: Cloudflare in front of the console challenged
    python-httpx when those were present (2026-08-25, from the Doha VM) and
    served the real login page for the same UA / `en-AE` / client-hints without
    them.
    """
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept-Language": _ACCEPT_LANGUAGE,
        "sec-ch-ua": (
            f'"Google Chrome";v="{_CHROME_MAJOR}", '
            f'"Chromium";v="{_CHROME_MAJOR}", "Not A(Brand";v="8"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    if navigate:
        headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        )
        headers["Upgrade-Insecure-Requests"] = "1"
    else:
        headers["Accept"] = "application/json, text/plain, */*"
    if referer:
        headers["Referer"] = referer
    return headers


def _is_cloudflare_challenge(response: httpx.Response) -> bool:
    text = response.text or ""
    return "Just a moment..." in text or "cf-challenge" in text.lower()


def _still_on_login(response: httpx.Response) -> bool:
    """Laravel re-renders `/login` on a rejected sign-in; that is a 200."""
    path = httpx.URL(str(response.url)).path.rstrip("/")
    if path.endswith("/login"):
        return True
    return 'id="recaptcha_token"' in (response.text or "")


def _recaptcha_origin(console_base: str) -> str:
    parsed = urlparse(console_base)
    host = parsed.hostname or "console.foodics.com"
    return f"{parsed.scheme or 'https'}://{host}:443"


async def _fetch_recaptcha_token(*, sitekey: str, origin: str, timeout: float) -> str:
    """The v3 token grecaptcha.execute would put in the login form.

    Two calls, as the login page's own script makes them: load the widget
    (anchor) then exchange it (reload), with action `homepage` and `hl=en-AE`.
    """
    co = base64.b64encode(origin.encode()).decode().rstrip("=") + "."
    chrome = _chrome_headers()
    chrome["Accept-Language"] = _ACCEPT_LANGUAGE
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=chrome
    ) as client:
        api_js = await client.get(
            f"https://www.google.com/recaptcha/api.js?render={sitekey}",
            headers={**chrome, "Referer": "https://console.foodics.com/login"},
        )
        version_m = _RECAPTCHA_VERSION_RE.search(api_js.text)
        if not version_m:
            return ""
        version = version_m.group(1)
        anchor = await client.get(
            "https://www.google.com/recaptcha/api2/anchor",
            params={
                "ar": "1",
                "k": sitekey,
                "co": co,
                "hl": "en-AE",
                "v": version,
                "size": "invisible",
                "sa": "homepage",
                "cb": "cb1",
            },
            headers={
                **chrome,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Referer": "https://console.foodics.com/login",
            },
        )
        token_m = _RECAPTCHA_ANCHOR_TOKEN_RE.search(anchor.text)
        if not token_m:
            return ""
        reload = await client.post(
            f"https://www.google.com/recaptcha/api2/reload?k={sitekey}",
            data={
                "v": version,
                "reason": "q",
                "c": token_m.group(1),
                "k": sitekey,
                "co": co,
                "hl": "en-AE",
                "size": "invisible",
                "sa": "homepage",
            },
            headers={
                **chrome,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.google.com",
                "Referer": str(anchor.url),
                "Accept": "*/*",
            },
        )
        rresp = _RECAPTCHA_RRESP_RE.search(reload.text)
        return rresp.group(1) if rresp else ""


class FoodicsError(RuntimeError):
    """A Foodics call that did not do what we asked."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403, 419)


class FoodicsAuthError(FoodicsError):
    """A login/session problem — a dead session, a rejected sign-in, or a
    Cloudflare challenge in front of the console. Distinct so a caller can tell
    "could not sign in" from "the order call failed"; still a `FoodicsError`, so
    the write-back records it either way."""


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
        self._user_id: str | None = None
        self._void_reason_id_cached: str | None = None
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
        self._user_id = None
        self._void_reason_id_cached = None

    def reset_login_backoff(self) -> None:
        """Clear the failed-login cooldown so the next call retries immediately."""
        self._login_error = None
        self._login_retry_at = None

    # ── the session ───────────────────────────────────────────────────────────

    async def _recaptcha_token(self, page_html: str) -> str:
        """The reCAPTCHA v3 token the login form posts as `token`.

        The live page runs `grecaptcha.execute(sitekey, { action: 'homepage' })`
        and writes the result into `#recaptcha_token`. We make the same two
        Google calls that script makes (anchor, then reload), as Chrome in the
        UAE. An empty return is what the page itself does when execute fails —
        the form still submits.
        """
        sitekey_m = _SITEKEY_RE.search(page_html)
        if not sitekey_m:
            logger.warning("Foodics login page had no reCAPTCHA site key")
            return ""
        origin = _recaptcha_origin(self.config.console_base)
        try:
            return await _fetch_recaptcha_token(
                sitekey=sitekey_m.group(1),
                origin=origin,
                timeout=max(self.config.timeout, _LOGIN_TIMEOUT),
            )
        except httpx.HTTPError as exc:
            logger.warning("Foodics reCAPTCHA token request failed: %s", exc)
            return ""

    async def _login(self) -> None:
        """Sign in to the console and cache the session cookie + CSRF token."""
        config = self.config
        timeout = max(config.timeout, _LOGIN_TIMEOUT)
        async with httpx.AsyncClient(
            timeout=timeout,
            base_url=config.console_base,
            follow_redirects=True,
            headers=_chrome_headers(navigate=True),
        ) as client:
            page = await client.get("/login")
            if _is_cloudflare_challenge(page):
                raise FoodicsAuthError(
                    "Foodics login page was challenged",
                    status=page.status_code,
                )
            if page.status_code >= 400:
                raise FoodicsAuthError(
                    f"Foodics login page failed: {page.status_code}",
                    status=page.status_code,
                )

            form_token = _FORM_TOKEN_RE.search(page.text)
            meta_token = _CSRF_META_RE.search(page.text)
            if form_token:
                csrf_token = form_token.group(1)
            elif meta_token:
                csrf_token = meta_token.group(1)
            else:
                csrf_token = client.cookies.get("XSRF-TOKEN")

            recaptcha = await self._recaptcha_token(page.text)

            resp = await client.post(
                "/login",
                data={
                    "business": config.account_number,
                    "email": config.email,
                    "password": config.password,
                    "token": recaptcha,
                    "_token": csrf_token,
                },
                headers=_chrome_headers(
                    navigate=True,
                    referer=config.console_base + "/login",
                )
                | {"Origin": config.console_base},
            )
            if _is_cloudflare_challenge(resp):
                raise FoodicsAuthError(
                    "Foodics login was challenged",
                    status=resp.status_code,
                )
            if resp.status_code >= 400 or _still_on_login(resp):
                raise FoodicsAuthError(
                    f"Foodics login failed: {resp.status_code} {resp.text[:200]}",
                    status=resp.status_code,
                )

            self._cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            fresh = _CSRF_META_RE.search(resp.text)
            self._csrf = (fresh.group(1) if fresh else None) or csrf_token
            logger.info("Foodics: signed in as %s", config.email)

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
                **_chrome_headers(referer=config.console_base + "/"),
                "Cookie": self._cookie or "",
                "X-Requested-With": "XMLHttpRequest",
            }
            if self._csrf:
                headers["X-CSRF-TOKEN"] = self._csrf
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            try:
                async with httpx.AsyncClient(
                    timeout=config.timeout,
                    base_url=config.console_base,
                    headers=_chrome_headers(),
                ) as client:
                    response = await client.request(
                        method, path, params=params, json=json_body, headers=headers
                    )
            except httpx.HTTPError as exc:
                last = exc
                if attempt + 1 < attempts:
                    continue
                raise FoodicsError(f"Foodics is unreachable: {exc}") from exc

            if _is_cloudflare_challenge(response):
                last = FoodicsError(
                    "Foodics returned a Cloudflare challenge",
                    status=response.status_code,
                )
                if attempt + 1 < attempts:
                    continue
                raise last

            if response.status_code in _SESSION_DEAD and attempt + 1 < attempts:
                # Session expired mid-use. Drop it and log in again on the next go.
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

    async def accept_order(self, order_id: str) -> Any:
        """Accept a still-pending order (`status = 2`). The console's Accept
        button; dispatch is a second step and is refused until this lands."""
        return await self._update(
            order_id, {"status": STATUS_ACTIVE, "accepted_at": _stamp()}
        )

    async def decline_order(self, order_id: str) -> Any:
        """Decline a still-pending order (`status = 3`). The console's Decline
        on a pending API order; Void is a different status and a different
        button, used once the order is already accepted."""
        return await self._update(order_id, {"status": STATUS_DECLINED})

    async def close_order(self, order_id: str) -> Any:
        """Close an accepted order (`status = 4` Done). The console's Close
        Order button, captured 2026-08-25 on Keeta 17423."""
        data: dict[str, Any] = {"status": STATUS_CLOSED, "closed_at": _stamp()}
        user_id = await self._whoami_id()
        if user_id:
            data["closer_id"] = user_id
        return await self._update(order_id, data)

    async def void_order(self, order_id: str) -> Any:
        """Void an accepted order (`status = 7`). The console's Void Order
        button — reason + reversing payment — from `useOrderActions.void_`.

        Pending orders are declined (`status = 3`) instead; this is the
        accepted/dispatched path an MM POS cancel has to take.
        """
        order = await self._order_for_write(order_id)
        if order is None:
            raise FoodicsError("Foodics no longer has this order", status=404)
        user_id = await self._whoami_id()
        reason_id = await self._void_reason_id()
        now = _stamp()
        payload = {
            "status": STATUS_VOID,
            "business_date": order.get("business_date"),
            "closer_id": user_id,
            "closed_at": now,
            "charges": [],
            "total_price": 0,
            "subtotal_price": 0,
            "discount_amount": 0,
            "tax_exclusive_discount_amount": 0,
            "payments": _void_payments(order, user_id=user_id, added_at=now),
            "meta": order.get("meta") or {},
            "products": _void_products(
                order, reason_id=reason_id, voider_id=user_id, closed_at=now
            ),
            "combos": _void_combos(
                order, reason_id=reason_id, voider_id=user_id, closed_at=now
            ),
        }
        return await self._update(order_id, payload)

    async def _whoami_id(self) -> str | None:
        """The signed-in console user's id (`closer_id` / `voider_id`)."""
        if self._user_id:
            return self._user_id
        payload = await self._call("GET", _GETTING, params={"url": "/whoami"})
        data = payload.get("data") if isinstance(payload, dict) else None
        user_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(user_id, str) and user_id:
            self._user_id = user_id
        return self._user_id

    async def _void_reason_id(self) -> str:
        """A type-1 (void/return) reason. Prefer `Customer Cancelled`."""
        if self._void_reason_id_cached:
            return self._void_reason_id_cached
        payload = await self._call(
            "GET",
            _SELECT_LISTING,
            params={
                "url": "/reasons",
                "filters[type]": 1,
                "filters[is_deleted]": "false",
                "page": 1,
            },
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise FoodicsError("Foodics has no void reasons configured")
        named = next(
            (
                row
                for row in rows
                if isinstance(row, dict)
                and (row.get("name") or "").strip() == _VOID_REASON_NAME
            ),
            None,
        )
        chosen = named if isinstance(named, dict) else rows[0]
        reason_id = chosen.get("id") if isinstance(chosen, dict) else None
        if not isinstance(reason_id, str) or not reason_id:
            raise FoodicsError("Foodics void reason was missing an id")
        self._void_reason_id_cached = reason_id
        return reason_id

    async def _order_for_write(self, order_id: str) -> dict | None:
        """The order plus products/payments/combos the Void payload copies."""
        params: dict[str, Any] = {
            "url": "/orders",
            "id": order_id,
            "include[]": [
                "branch",
                "products.product",
                "payments.paymentMethod",
                "payments.user",
                "combos.products.product",
                "combos.comboSize",
            ],
        }
        try:
            payload = await self._call("GET", _GETTING, params=params)
        except FoodicsError as exc:
            if exc.status == 404:
                return None
            raise
        return _order_of(payload)

    async def _update(self, order_id: str, data: dict[str, Any]) -> Any:
        # Captured from the console on 2026-08-25: PUT `/core-api/updating` with
        # `{url: /orders/<id>, payload: {...}}`. `{url: /orders, id, data}` hits
        # the collection and 405s (`v5/orders` is GET/HEAD/POST only).
        return await self._call(
            "PUT",
            _UPDATING,
            json_body={"url": f"/orders/{order_id}", "payload": data},
        )

    # ── Menu / price tag (catalog sync) ──────────────────────────────────────
    # Verified against the live console API 2026-08-31: the aggregator menu for
    # the integrated branches IS the "Grubtech" price tag — its products carry the
    # aggregator price in `pivot.price` (distinct from the product's own `price`),
    # and its modifier_options the variant prices. `GET /core-api/listing?url=
    # /price_tags/<id>/products&page=N` → `{data:[...], meta:{per_page, to, ...}}`.

    async def _list_all(self, resource: str, *, cap_pages: int = 20) -> list[dict]:
        """Every page of a `listing` resource. Meta carries per_page/to (no
        last_page), so read until a short page ends it."""
        rows: list[dict] = []
        page = 1
        while page <= cap_pages:
            payload = await self._call(
                "GET", _LISTING, params={"url": resource, "page": page}
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            if not data:
                break
            rows.extend(data)
            per_page = (payload.get("meta") or {}).get("per_page") or 50
            if len(data) < per_page:
                break
            page += 1
        return rows

    async def list_price_tag_products(self, price_tag_id: str) -> list[dict]:
        """The price tag's products — each with `price`, `pivot.price`, `name`,
        `name_localized`, `is_active`, `sku`, `id`."""
        return await self._list_all(f"/price_tags/{price_tag_id}/products")

    async def list_price_tag_modifier_options(self, price_tag_id: str) -> list[dict]:
        """The price tag's modifier options (the variant/size prices), each with
        `name`, `price`, `pivot.price`."""
        return await self._list_all(f"/price_tags/{price_tag_id}/modifier_options")

    async def list_categories(self) -> list[dict]:
        """The menu categories (`{id, name, ...}`) — a product create needs the
        `category_id`, which is the menu category (distinct from the Grubtech
        subgroup a product also joins)."""
        return await self._list_all("/categories")

    async def category_id_by_name(self, name: str) -> str | None:
        """Resolve a menu-category name to its Foodics id (case-insensitive)."""
        target = (name or "").strip().casefold()
        for cat in await self.list_categories():
            if str(cat.get("name", "")).strip().casefold() == target:
                return cat.get("id")
        return None

    async def set_price_tag_product_price(
        self, price_tag_id: str, product_id: str, price: Any
    ) -> Any:
        """Set one product's aggregator price on the price tag (the `pivot.price`).

        The write mirror of the read above. Same `PUT /core-api/updating` shape as
        the order write; only ever called behind `CATALOG_SYNC_ENABLED`. The exact
        payload key is confirmed at enablement against a live session — kept in one
        place so that confirmation is a one-line change, not a hunt.
        """
        return await self._call(
            "PUT",
            _UPDATING,
            json_body={
                "url": f"/price_tags/{price_tag_id}/products/{product_id}",
                "payload": {"price": price},
            },
        )

    async def remove_price_tag_product(self, price_tag_id: str, product_id: str) -> Any:
        """Remove one product from the price tag — the reversible "delete from the
        aggregator menu" (drops its `pivot`, so it stops being on the marketplaces)
        that does NOT deactivate the product for the POS. Same `/price_tags/{tag}/
        products/{id}` URL as the price setter, via the `deleting` verb. Only ever
        reached behind `CATALOG_SYNC_ENABLED`; the exact envelope is confirmed
        against one controlled removal at enablement (same discipline as the create
        and price writers), kept here so that is a one-line change."""
        return await self._call(
            "DELETE",
            _DELETING,
            json_body={"url": f"/price_tags/{price_tag_id}/products/{product_id}"},
        )

    # ── create (catalog sync writer) ─────────────────────────────────────────
    # Foodics is the master for the two integrated branches: a product created
    # here, added to its Grubtech category subgroup and given a Grubtech price-tag
    # price, is pushed by Foodics to *every* marketplace — so "create an item on
    # the aggregators" is one Foodics create, not five portal creates. The verb is
    # `creating` (verified 2026-09-01, see `_CREATING`); the envelope is the
    # `{url, payload}` the console uses for `updating`. Every method here is only
    # ever reached behind `CATALOG_SYNC_ENABLED`; the exact create-payload keys are
    # confirmed against one controlled create at enablement (same discipline as
    # `set_price_tag_product_price`), kept in one place so that is a one-line change.

    async def _create(self, resource: str, payload: dict[str, Any]) -> Any:
        """`POST /core-api/creating` with `{url: <resource>, payload}` — the create
        mirror of `_update`."""
        return await self._call(
            "POST", _CREATING, json_body={"url": resource, "payload": payload}
        )

    async def create_product(
        self,
        *,
        name: str,
        price: Any,
        category_id: str,
        name_localized: str | None = None,
        sku: str | None = None,
        subgroup_id: str | None = None,
        aggregator_price: Any | None = None,
        tax_group_id: str = FOODICS_VAT_TAX_GROUP_ID,
    ) -> Any:
        """Create a menu product and, in the same call, place it in a Grubtech
        subgroup and give it the Grubtech price-tag price — so Foodics syncs it to
        the marketplaces.

        `aggregator_price` defaults to `price`: strict parity is the rule, and the
        price tag must never quote a figure the product itself does not. The
        method/tax constants are the account's own (read live), echoed because a
        create that omits them is rejected.
        """
        agg_price = price if aggregator_price is None else aggregator_price
        payload: dict[str, Any] = {
            "name": name,
            "name_localized": name_localized or name,
            "price": price,
            "category_id": category_id,
            "tax_group_id": tax_group_id,
            "pricing_method": FOODICS_PRICING_METHOD,
            "selling_method": FOODICS_SELLING_METHOD,
            "costing_method": FOODICS_COSTING_METHOD,
            "is_active": True,
            "is_ready": True,
        }
        if sku:
            payload["sku"] = sku
        if subgroup_id:
            payload["groups"] = [{"id": subgroup_id, "is_active": True}]
        payload["price_tags"] = [
            {"id": FOODICS_GRUBTECH_PRICE_TAG_ID, "price": agg_price}
        ]
        return await self._create("/products", payload)

    async def add_product_to_grubtech(self, product_id: str, subgroup_id: str) -> Any:
        """Attach an existing product to a Grubtech category subgroup (its
        membership pivot) — for a product that already exists but is not yet on the
        aggregator menu. Read shape: `groups:[{id, pivot:{is_active}}]`."""
        return await self._call(
            "PUT",
            _UPDATING,
            json_body={
                "url": f"/products/{product_id}",
                "payload": {"groups": [{"id": subgroup_id, "is_active": True}]},
            },
        )

    async def get_product(self, product_id: str) -> dict | None:
        """One product's full record (`category`, `groups`, `price_tags`, …) — used
        to confirm a create landed and to read back the id the mapping stores."""
        payload = await self._call(
            "GET", _GETTING, params={"url": "/products", "id": product_id}
        )
        return _order_of(payload)


def _order_of(payload: Any) -> dict | None:
    """The single order out of a `getting` response (`{data: {...}}`), tolerating
    the list shape a filtered read can return."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        return data[0] if data else None
    return data or payload


def _payment_method_id(payment: dict) -> str | None:
    method = payment.get("payment_method") or payment.get("paymentMethod") or {}
    if isinstance(method, dict) and method.get("id"):
        return str(method["id"])
    raw = payment.get("payment_method_id")
    return str(raw) if raw else None


def _void_payments(order: dict, *, user_id: str | None, added_at: str) -> list[dict]:
    """Existing payments plus the reversing row the Void modal adds."""
    existing: list[dict] = []
    paid = 0.0
    method_id: str | None = None
    for payment in order.get("payments") or []:
        if not isinstance(payment, dict):
            continue
        pid = _payment_method_id(payment)
        if pid:
            method_id = pid
        amount = payment.get("amount") or 0
        try:
            paid += float(amount)
        except (TypeError, ValueError):
            pass
        user = payment.get("user") if isinstance(payment.get("user"), dict) else {}
        existing.append(
            {
                "payment_method_id": pid,
                "user_id": user.get("id"),
                "amount": amount,
                "tendered": payment.get("tendered"),
                "business_date": payment.get("business_date"),
                "added_at": payment.get("added_at"),
            }
        )
    if method_id and paid:
        existing.append(
            {
                "payment_method_id": method_id,
                "user_id": user_id,
                "amount": -paid,
                "tendered": -paid,
                "business_date": order.get("business_date"),
                "added_at": added_at,
            }
        )
    return existing


def _void_products(
    order: dict, *, reason_id: str, voider_id: str | None, closed_at: str
) -> list[dict]:
    rows: list[dict] = []
    for product in order.get("products") or []:
        if not isinstance(product, dict):
            continue
        nested = (
            product.get("product") if isinstance(product.get("product"), dict) else {}
        )
        rows.append(
            {
                "id": product.get("id"),
                "product_id": nested.get("id"),
                "quantity": product.get("quantity"),
                "unit_price": product.get("unit_price"),
                "total_price": product.get("total_price"),
                "tax_exclusive_total_price": product.get("tax_exclusive_total_price"),
                "tax_exclusive_unit_price": product.get("tax_exclusive_unit_price"),
                "discount_amount": product.get("discount_amount"),
                "discount_type": product.get("discount_type"),
                "status": PRODUCT_VOID,
                "void_reason_id": reason_id,
                "voider_id": voider_id,
                "closed_at": closed_at,
                "kitchen_notes": product.get("kitchen_notes"),
                "added_at": product.get("added_at"),
                "taxes": [],
            }
        )
    return rows


def _void_combos(
    order: dict, *, reason_id: str, voider_id: str | None, closed_at: str
) -> list[dict]:
    rows: list[dict] = []
    for combo in order.get("combos") or []:
        if not isinstance(combo, dict):
            continue
        size = combo.get("combo_size") or combo.get("comboSize") or {}
        products: list[dict] = []
        for nested in combo.get("products") or []:
            if not isinstance(nested, dict):
                continue
            product = (
                nested.get("product") if isinstance(nested.get("product"), dict) else {}
            )
            products.append(
                {
                    "id": nested.get("id"),
                    "product_id": product.get("id"),
                    "quantity": nested.get("quantity"),
                    "unit_price": nested.get("unit_price"),
                    "total_price": nested.get("total_price"),
                    "status": PRODUCT_VOID,
                    "void_reason_id": reason_id,
                    "voider_id": voider_id,
                    "closed_at": closed_at,
                    "taxes": [],
                }
            )
        rows.append(
            {
                "combo_size_id": size.get("id") if isinstance(size, dict) else None,
                "quantity": combo.get("quantity"),
                "discount_amount": combo.get("discount_amount"),
                "products": products,
            }
        )
    return rows


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
