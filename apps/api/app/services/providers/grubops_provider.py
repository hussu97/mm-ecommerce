"""
Talking to GrubTech's GrubOps console, which has no public API.

GrubOps 2.0 is a Flutter app talking to `internal-api.grubtech.io`, and there is
no partner API, no key to be issued, and nobody to ask for one. What there *is*
is an ordinary Cognito user pool and an ordinary REST service behind it, so this
module logs in the way the console does and calls the same endpoints. That is a
real dependency on somebody else's private interface and it is treated as one:
everything GrubOps-shaped is behind this file, the whole feature is off unless
`GRUBOPS_SYNC_ENABLED` says otherwise, and the failure mode is a log line rather
than a customer waiting. Official partner access is the sustainable version of
this and should replace the login below the moment it exists.

**Auth is two steps that turned out to be one.** The console signs in against
Cognito and then posts the resulting token to `admin-user-auth` to trade it for
a GrubTech session. The trade is unnecessary for our purposes: the Cognito
`IdToken` is accepted directly as `Authorization: Bearer` by the services this
sync uses, and it already carries the claims they check — `partner_id`,
`brandIds`, `locationIds`. So there is one call to get a token and none to
convert it. Verified against the live account on 2026-08-23.

**The token lasts an hour, and an hour expires mid-sweep.** Two guards, because
either alone leaves a gap. The cache refreshes at `expires_at` minus a couple of
minutes so an ordinary call never races the clock; and `_call` treats a 401 as
"the token died anyway" and retries once with a fresh one, which is what covers
a reconcile that started at 59 minutes. The token is re-read from the cache on
every attempt rather than captured once at the top, or the refresh would be
written and then not used.

`USER_PASSWORD_AUTH` against the pool directly is what the app is configured for
— no SRP, no MFA challenge, no hosted-UI redirect — so this is a plain POST to
`cognito-idp` and needs no AWS SDK. `httpx` is already here; an AWS SDK is not,
and adding one to sign an unsigned public call would be a dependency for nothing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Retried once. The same set as every other provider here — the codes that
#: mean "ask again", not "you asked wrongly".
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: The services this sync touches, mounted as path prefixes on one host.
_AVAILABILITY = "/item-availability-mgt"
_LOCATIONS = "/location-mgt"
_ITEM_SEARCH = "/item-search"

#: Cut the cached token's life short by this much. A call that begins inside the
#: margin still has the whole margin to finish in, which is the point: the
#: alternative is a token that was valid when the request was built and expired
#: while it was in flight.
_EXPIRY_SKEW = timedelta(seconds=120)

#: After Cognito answers TooManyRequests, stop attempting auth for this long so the
#: throttle can reset. Without it, the 30s orders sweep re-logs-in on every failed
#: tick (the failed login never caches a token), which keeps Cognito's rate limit hot
#: forever — the self-sustaining storm behind the 2026-09-01 GrubOps→POS outage. Must
#: comfortably exceed Cognito's throttle window; the sweep just fails fast meanwhile.
_AUTH_COOLDOWN = timedelta(seconds=120)


def _is_throttle(exc: Exception) -> bool:
    """Whether a Cognito refusal is its rate limit (`TooManyRequestsException`)."""
    return "TooManyRequests" in str(exc)


#: What GrubOps calls a menu line. Mirrored from `models.grubops` rather than
#: imported, so the transport layer stays free of the ORM.
TYPE_RECIPE = "RECIPE"
TYPE_MODIFIER = "MODIFIER"
TYPE_NESTED_MODIFIER = "NESTED_MODIFIER"

#: The two ways an item can be out. "Until further notice" carries no date;
#: the other one must.
STATUS_UNTIL_FURTHER_NOTICE = "UNAVAILABLE_UNTIL_FURTHER_NOTICE"
STATUS_UNTIL_SPECIFIC_DATE = "UNAVAILABLE_UNTIL_SPECIFIC_DATE"

#: The order read endpoints, mounted on the orders host. GrubOps' order-management
#: *write* overrides are no longer called from here — MM drives the order forward
#: on Foodics instead (see `foodics_orders_service`).
_ORDERS = "/v2.0"


class GrubOpsError(RuntimeError):
    """A call to GrubOps that did not do what we asked.

    `is_auth` is the distinction the retry loop cares about: a 401 is worth one
    more try with a new token, where a 400 is a payload we built wrongly and
    will build wrongly again.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)


@dataclass(frozen=True)
class GrubOpsConfig:
    username: str
    password: str
    cognito_client_id: str
    cognito_region: str
    partner_id: str
    api_base: str
    catalog_api_base: str
    orders_base: str
    source: str
    timeout: float

    @property
    def item_search_base(self) -> str:
        """The item-search service, mounted as a prefix on the main host."""
        return self.api_base + _ITEM_SEARCH

    @property
    def cognito_host(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/"

    @property
    def is_configured(self) -> bool:
        """Whether we can talk to GrubOps at all.

        Credentials only. Whether any *item* can be pushed is a question about
        the mapping tables, asked per item and answered somewhere else.
        """
        return bool(self.username and self.password and self.cognito_client_id)


def _config() -> GrubOpsConfig:
    return GrubOpsConfig(
        username=settings.GRUBOPS_USERNAME,
        password=settings.GRUBOPS_PASSWORD,
        cognito_client_id=settings.GRUBOPS_COGNITO_CLIENT_ID,
        cognito_region=settings.GRUBOPS_COGNITO_REGION,
        partner_id=settings.GRUBOPS_PARTNER_ID,
        api_base=settings.GRUBOPS_API_BASE,
        catalog_api_base=settings.GRUBOPS_CATALOG_API_BASE,
        orders_base=settings.GRUBOPS_ORDERS_API_BASE,
        source=settings.GRUBOPS_SOURCE,
        timeout=settings.GRUBOPS_TIMEOUT_SECONDS,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GrubOpsClient:
    """Every GrubOps endpoint this sync uses, one method each."""

    def __init__(self, config: GrubOpsConfig | None = None) -> None:
        self._config = config
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: datetime | None = None
        #: Single-flight auth: concurrent callers (the orders + reconcile sweeps) must
        #: not each fire their own Cognito login on a cold cache — that burst is what
        #: trips the rate limit on a restart. Lazily created so the module-level
        #: singleton can be constructed without a running loop.
        self._auth_lock: asyncio.Lock | None = None
        #: While set (and in the future), auth is in a post-throttle cooldown: token()
        #: fails fast without calling Cognito, so the throttle can reset.
        self._cooldown_until: datetime | None = None

    def _lock(self) -> asyncio.Lock:
        if self._auth_lock is None:
            self._auth_lock = asyncio.Lock()
        return self._auth_lock

    @property
    def config(self) -> GrubOpsConfig:
        # Read through to settings so a test can patch the environment without
        # rebuilding the module-level singleton.
        return self._config or _config()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def reset(self) -> None:
        """Forget the cached token. For a credential rotation, and for tests."""
        self._id_token = None
        self._refresh_token = None
        self._expires_at = None

    # ── the token ─────────────────────────────────────────────────────────────

    async def _cognito(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One unsigned POST to the Cognito IDP endpoint."""
        config = self.config
        try:
            async with httpx.AsyncClient(timeout=config.timeout) as client:
                response = await client.post(
                    config.cognito_host,
                    json=payload,
                    headers={
                        "Content-Type": "application/x-amz-json-1.1",
                        "X-Amz-Target": (
                            "AWSCognitoIdentityProviderService.InitiateAuth"
                        ),
                    },
                )
        except httpx.HTTPError as exc:
            raise GrubOpsError(f"Cognito is unreachable: {exc}") from exc

        if response.status_code != 200:
            # Cognito puts the useful part in `__type`; the body is small and
            # worth keeping whole, because "NotAuthorizedException" and
            # "UserNotFoundException" are two very different Monday mornings.
            raise GrubOpsError(
                f"Cognito refused the login: {response.text[:200]}",
                status=response.status_code,
            )

        result = response.json().get("AuthenticationResult") or {}
        if not result.get("IdToken"):
            # A challenge — MFA, a forced password change. Not something this
            # can answer, and saying so plainly beats a KeyError three frames up.
            raise GrubOpsError(
                "Cognito returned a challenge rather than a token; "
                "the GrubOps login needs attention"
            )
        return result

    def _store(self, result: dict[str, Any]) -> None:
        self._cooldown_until = None  # a live token clears any throttle cooldown
        self._id_token = result["IdToken"]
        # Absent on a refresh — the original refresh token stays valid, so only
        # overwrite when a new one is actually offered.
        self._refresh_token = result.get("RefreshToken") or self._refresh_token
        self._expires_at = (
            _now()
            + timedelta(seconds=int(result.get("ExpiresIn", 3600)))
            - _EXPIRY_SKEW
        )

    async def _login(self) -> None:
        config = self.config
        self._store(
            await self._cognito(
                {
                    "AuthFlow": "USER_PASSWORD_AUTH",
                    "ClientId": config.cognito_client_id,
                    "AuthParameters": {
                        "USERNAME": config.username,
                        "PASSWORD": config.password,
                    },
                }
            )
        )
        logger.info("GrubOps: signed in as %s", config.username)

    async def _refresh(self) -> None:
        """Trade the refresh token for a new id token, or start over."""
        if not self._refresh_token:
            await self._login()
            return
        try:
            self._store(
                await self._cognito(
                    {
                        "AuthFlow": "REFRESH_TOKEN_AUTH",
                        "ClientId": self.config.cognito_client_id,
                        "AuthParameters": {"REFRESH_TOKEN": self._refresh_token},
                    }
                )
            )
        except GrubOpsError as exc:
            # Refresh tokens expire too, and a rotated password invalidates
            # everything. Falling back to a full login makes that self-healing
            # rather than an outage that needs a restart.
            logger.info("GrubOps: refresh failed (%s); signing in again", exc)
            self.reset()
            await self._login()

    def _token_is_live(self) -> bool:
        return self._id_token is not None and (
            self._expires_at is None or _now() < self._expires_at
        )

    async def token(self, *, force: bool = False) -> str:
        """The current id token, minted or refreshed as needed.

        Single-flighted and throttle-aware: concurrent callers coalesce behind one
        auth, and after Cognito answers TooManyRequests we back off for
        `_AUTH_COOLDOWN` rather than re-logging-in on every 30s sweep tick (which is
        what kept the throttle hot in the 2026-09-01 outage).
        """
        if not self.is_configured:
            raise GrubOpsError("GrubOps is not configured")
        # Fast path: a live cached token, no lock contention on the hot path.
        if not force and self._token_is_live():
            return self._id_token  # type: ignore[return-value]
        async with self._lock():
            # Re-check under the lock — another caller may have just authed.
            if not force and self._token_is_live():
                return self._id_token  # type: ignore[return-value]
            if self._cooldown_until is not None and _now() < self._cooldown_until:
                raise GrubOpsError(
                    "GrubOps auth is cooling down after Cognito throttling; "
                    f"next attempt after {self._cooldown_until:%H:%M:%S}Z"
                )
            try:
                if force or self._id_token is None:
                    await (self._refresh() if force else self._login())
                elif self._expires_at is not None and _now() >= self._expires_at:
                    await self._refresh()
            except GrubOpsError as exc:
                if _is_throttle(exc):
                    self._cooldown_until = _now() + _AUTH_COOLDOWN
                    logger.warning(
                        "GrubOps: Cognito throttled the login; pausing auth until %s",
                        self._cooldown_until,
                    )
                raise
        assert self._id_token is not None  # noqa: S101 — narrowed by the branches
        return self._id_token

    # ── transport ─────────────────────────────────────────────────────────────

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        base: str | None = None,
        timeout: float | None = None,
        attempts: int = 2,
    ) -> Any:
        config = self.config
        if not config.is_configured:
            raise GrubOpsError("GrubOps is not configured")

        last: Exception | None = None
        refreshed = False

        for attempt in range(attempts):
            # Re-read every attempt: a 401 below may have replaced the token
            # since this loop started, and pinning it once would discard that.
            bearer = await self.token(force=refreshed and attempt > 0)
            try:
                async with httpx.AsyncClient(
                    timeout=timeout or config.timeout
                ) as client:
                    response = await client.request(
                        method,
                        (base or config.api_base) + path,
                        json=json_body,
                        params=params,
                        headers={
                            "Authorization": f"Bearer {bearer}",
                            "Accept": "application/json",
                        },
                    )
            except httpx.HTTPError as exc:
                last = exc
                if attempt + 1 < attempts:
                    continue
                raise GrubOpsError(f"GrubOps is unreachable: {exc}") from exc

            if response.status_code in (401, 403) and not refreshed:
                # The hour ran out mid-sweep. Worth exactly one more try, and
                # only one — a permission problem would loop forever otherwise.
                refreshed = True
                last = GrubOpsError(
                    "GrubOps rejected the token", status=response.status_code
                )
                if attempt + 1 < attempts:
                    continue
                # No attempts left to spend on the refresh; take one anyway,
                # so the *next* call starts with a live token.
                await self.token(force=True)
                raise last

            if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                last = GrubOpsError(
                    f"GrubOps returned {response.status_code}",
                    status=response.status_code,
                )
                continue

            return _unwrap(response)

        raise GrubOpsError(str(last) if last else "GrubOps call failed")

    # ── availability ──────────────────────────────────────────────────────────

    async def mark_unavailable(self, bodies: list[dict[str, Any]]) -> Any:
        """Take a batch of items off the aggregators."""
        return await self._call(
            "POST",
            f"{_AVAILABILITY}/v1.0/items-availability/unavailable",
            json_body=bodies,
        )

    async def mark_available(self, bodies: list[dict[str, Any]]) -> Any:
        """Put a batch of items back."""
        return await self._call(
            "POST",
            f"{_AVAILABILITY}/v1.0/items-availability/available",
            json_body=bodies,
        )

    async def read_availability(
        self,
        *,
        location_id: str,
        brand_id: str,
        item_type: str = TYPE_RECIPE,
        partner_id: str | None = None,
        language: str = "en",
    ) -> list[dict]:
        """What GrubOps currently believes is *unavailable* at one location.

        The same `list_items` call with the flag set, rather than the
        `items-availability/byItems` endpoint the older screen uses — that one
        rejects everything we have been able to build for it, and this returns
        the state anyway, alongside the names the seeder needs.

        Used by the drift check only. The desired state always comes from our
        own database, never from this.
        """
        return await self.list_items(
            location_id=location_id,
            brand_id=brand_id,
            item_type=item_type,
            unavailable_only=True,
            partner_id=partner_id,
            language=language,
        )

    # ── catalogue, for the seeder ────────────────────────────────────────────
    #
    # Three services on two hosts, which is GrubTech's split and not ours:
    # locations and the item list are mounted under `internal-api`, and
    # `serving-brands` answers only on the catalogue host. Hence `base=` per
    # call rather than one base for the whole client.

    async def list_locations(self, partner_id: str | None = None) -> list[dict]:
        """Every location on the account, which is how the branch map is checked."""
        payload = await self._call(
            "GET",
            f"{_LOCATIONS}/v1.0/locations/search/byPartnerId",
            params={"partnerId": partner_id or self.config.partner_id},
        )
        return (payload or {}).get("content") or []

    async def serving_brands(
        self, *, location_id: str, partner_id: str | None = None
    ) -> list[dict]:
        """The brands trading at one location.

        Needed because every availability payload carries a `brandId` and
        nothing on our side has one — this is the only place it comes from.
        """
        payload = await self._call(
            "GET",
            f"/partners/{partner_id or self.config.partner_id}"
            f"/locations/{location_id}/serving-brands",
            base=self.config.catalog_api_base,
        )
        return payload or []

    async def list_items(
        self,
        *,
        location_id: str,
        brand_id: str,
        item_type: str = TYPE_RECIPE,
        unavailable_only: bool = False,
        partner_id: str | None = None,
        language: str = "en",
        search_text: str = "",
    ) -> list[dict]:
        """
        The menu as GrubOps holds it for one location, with its availability.

        A **POST** to `/v2.0/items/`, which is neither of the two GET search
        paths also present in their bundle — those answer 404 on the
        item-search service and 500 on the catalogue host, and this is the one
        the console actually uses. Body and response shape both come from the
        bundle's own mappers.

        Each item carries `{id, type, name.translations, parentAssociations,
        childAssociations, unavailability, attributes}`. `unavailability` is
        null for anything on sale, and otherwise
        `{source, reason, status, unavailableUntil}` — note that those field
        names are the *response* spelling and differ from the ones a write
        takes.

        `item_type` is RECIPE or MODIFIER: they are two separate calls, and a
        location has far more of the second than the first.
        """
        payload = await self._call(
            "POST",
            "/v2.0/items/",
            json_body={
                "partnerId": partner_id or self.config.partner_id,
                "locationId": location_id,
                "brandIds": [brand_id],
                "searchText": search_text,
                "itemType": item_type,
                "unavailableItemsOnly": unavailable_only,
                "language": language,
            },
            base=self.config.item_search_base,
        )
        if isinstance(payload, dict):
            return payload.get("items") or []
        return payload or []

    # ── orders (aggregator ingest, read-only) ────────────────────────────────
    #
    # The order side answers on a third host, the console's own
    # `api-grubops.grubtech.io`, hence `base=self.config.orders_base` on each.
    # Reads are how MM learns of an aggregator sale. The write-back — dispatch,
    # finalise, cancel — is done on the *Foodics* order now (see
    # `foodics_orders_service`), not through GrubOps' `order-force-*` overrides,
    # which were removed: they were a blunt jump straight to "the order is ready"
    # that skipped the real ready-to-dispatch step, and Foodics exposes that step
    # properly.

    async def list_orders(
        self,
        *,
        statuses: list[str],
        size: int = 100,
        location_ids: list[str] | None = None,
        channel_ids: list[str] | None = None,
    ) -> list[dict]:
        """One page of the most-recent orders in the given statuses.

        `getOrderSummaryList` is a single most-recent window, not real
        pagination — `page` above 0 answers 404 — so there is one call and one
        `size`. An empty result comes back as HTTP 200 with an `errorCode` of
        404 ("No orders found"), which `_unwrap` raises; the caller treats that
        one code as an empty list rather than a failure.
        """
        try:
            payload = await self._call(
                "POST",
                f"{_ORDERS}/getOrderSummaryList/partnerId/{self.config.partner_id}",
                json_body={
                    "brandIds": [],
                    "locationIds": location_ids or [],
                    "channelIds": channel_ids or [],
                    "statuses": statuses,
                    "paymentMethods": [],
                    "orderTypes": [],
                    "isConsiderScheduledOrders": False,
                    "isConsiderFailedOrders": False,
                },
                params={"page": 0, "size": size},
                base=self.config.orders_base,
            )
        except GrubOpsError as exc:
            if exc.status == 404:
                return []
            raise
        if isinstance(payload, dict):
            return payload.get("orderSummaryList") or []
        return payload or []

    async def order_count(self, *, statuses: list[str]) -> dict:
        """A cheap `{status: count}` probe the loop uses to skip quiet ticks."""
        try:
            payload = await self._call(
                "POST",
                f"{_ORDERS}/getOrderCount/partnerId/{self.config.partner_id}",
                json_body={
                    "brandIds": [],
                    "locationIds": [],
                    "channelIds": [],
                    "statuses": statuses,
                    "paymentMethods": [],
                    "orderTypes": [],
                    "isConsiderScheduledOrders": False,
                    "isConsiderFailedOrders": False,
                },
                base=self.config.orders_base,
            )
        except GrubOpsError as exc:
            if exc.status == 404:
                return {}
            raise
        counts: dict[str, int] = {}
        for row in (payload or {}).get("orderStatusCountList") or []:
            counts[str(row.get("status"))] = int(row.get("count") or 0)
        return counts

    async def get_order(self, order_id: str) -> dict | None:
        """The full order: header, customer, delivery, lines, taxes, history.

        Returns the inner `orderInfo` object, which is what every consumer
        wants; `None` if GrubOps no longer has it.
        """
        try:
            payload = await self._call(
                "GET",
                f"{_ORDERS}/getOrderInfo/partnerId/{self.config.partner_id}"
                f"/orderInternalId/{order_id}",
                base=self.config.orders_base,
            )
        except GrubOpsError as exc:
            if exc.status == 404:
                return None
            raise
        if isinstance(payload, dict):
            return payload.get("orderInfo") or payload
        return None


def _unwrap(response: httpx.Response) -> Any:
    """The body, or an error carrying whatever GrubOps said was wrong.

    Their services answer a rejected payload with **HTTP 200** and an
    `errorCode` in the body, so status alone is not enough to tell a success
    from a failure and both have to be looked at.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.status_code >= 400:
        raise GrubOpsError(
            f"GrubOps returned {response.status_code}: {response.text[:200]}",
            status=response.status_code,
        )

    if isinstance(payload, dict) and payload.get("errorCode"):
        raise GrubOpsError(
            f"GrubOps rejected the request: "
            f"{payload.get('message') or payload.get('errors') or payload}",
            status=int(payload.get("errorCode") or 0) or None,
        )

    return payload


#: The one client. Holds the token cache, so sharing it is the point.
provider = GrubOpsClient()
