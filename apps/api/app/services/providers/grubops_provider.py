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
`cognito-idp` and needs no AWS SDK. `httpx` is already here; boto3 is not, and
adding it to sign an unsigned public call would be a dependency for nothing.
"""

from __future__ import annotations

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

#: What GrubOps calls a menu line. Mirrored from `models.grubops` rather than
#: imported, so the transport layer stays free of the ORM.
TYPE_RECIPE = "RECIPE"
TYPE_MODIFIER = "MODIFIER"
TYPE_NESTED_MODIFIER = "NESTED_MODIFIER"

#: The two ways an item can be out. "Until further notice" carries no date;
#: the other one must.
STATUS_UNTIL_FURTHER_NOTICE = "UNAVAILABLE_UNTIL_FURTHER_NOTICE"
STATUS_UNTIL_SPECIFIC_DATE = "UNAVAILABLE_UNTIL_SPECIFIC_DATE"


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
    source: str
    timeout: float

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

    async def token(self, *, force: bool = False) -> str:
        """The current id token, minted or refreshed as needed."""
        if not self.is_configured:
            raise GrubOpsError("GrubOps is not configured")
        if force:
            await self._refresh()
        elif self._id_token is None:
            await self._login()
        elif self._expires_at is not None and _now() >= self._expires_at:
            await self._refresh()
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

    async def read_availability(self, body: dict[str, Any]) -> Any:
        """What GrubOps currently believes about a location's items.

        Used by the drift check rather than the ordinary tick — the desired
        state always comes from our own database, never from this.
        """
        return await self._call(
            "POST",
            f"{_AVAILABILITY}/v1.0/items-availability/byItems",
            json_body=body,
        )

    # ── catalogue, for the seeder ────────────────────────────────────────────
    #
    # These answer on a different host from the availability writes
    # (`api-grubone` rather than `internal-api`), which is GrubTech's split and
    # not ours — hence `base=` on each call rather than one base for the client.

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

    async def search_menu_items(
        self, *, location_id: str, brand_ids: str, name: str = ""
    ) -> list[dict]:
        """The menu as GrubOps holds it for one location — the seeder's left hand.

        The exact query this wants is still to be confirmed against a captured
        console request; see `docs/grubops-integration.md`. It is isolated here
        so that confirming it is a one-line change rather than a hunt.
        """
        payload = await self._call(
            "GET",
            f"/v2.0/menu-items/searchMenuItem/byLocation/"
            f"{location_id}/byBrands/{brand_ids}",
            params={"name": name},
            base=self.config.catalog_api_base,
        )
        if isinstance(payload, dict):
            return payload.get("content") or []
        return payload or []

    async def search_modifiers(
        self, *, location_id: str, brand_ids: str, name: str = ""
    ) -> list[dict]:
        """The modifier options for one location. Same caveat as the menu above."""
        payload = await self._call(
            "GET",
            f"/v2.0/modifiers/searchModifier/byLocation/"
            f"{location_id}/byBrands/{brand_ids}",
            params={"name": name},
            base=self.config.catalog_api_base,
        )
        if isinstance(payload, dict):
            return payload.get("content") or []
        return payload or []


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
