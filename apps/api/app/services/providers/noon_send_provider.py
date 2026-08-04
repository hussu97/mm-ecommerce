"""
noon Send (Rider-on-Demand) public API client.

Transport only: headers, retries and error shape. What we send and what we do
with the answer lives in `noon_send_service`, the same split the Lalamove pair
uses.

Three things about this API shape everything downstream.

**There is no quotation endpoint.** Not at task creation, not on the task
details, not anywhere. The price is the published rate card and nothing else, so
what a run cost us is always something we computed rather than something they
told us.

**Coordinates are integers.** Latitude and longitude are sent as the decimal
degree multiplied by 10^7 — 25.3304139 is `253304139`. Sending a float is a 422.

**Money is fils.** `cod_value` and `prepaid_value` are integers of 1/100 AED,
and exactly one of the two may be non-zero.

Authentication is a plain `X-API-Key` header rather than a signature, in both
directions: the status webhook they POST to us carries the key we gave them and
nothing else, so there is no body to verify.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "NoonSendClient",
    "NoonSendError",
    "coordinate",
    "fils",
    "provider",
]

HOSTS = {
    "staging": "https://food-api-team.noonstg.team",
    "production": "https://food-api-team.noon.team",
}

#: Errors worth trying again — a fleet service that is briefly unreachable
#: should not fail a dispatch, and a 429 is a queue, not a refusal.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Substrings that mean "we cannot carry this one", as opposed to "something
#: went wrong". Matched against the message because the API returns FastAPI
#: validation shapes rather than machine-readable error ids. Any of these sends
#: the order to Lalamove instead of leaving it stranded.
_UNSERVICEABLE_MARKERS = (
    "distance",
    "not serviceable",
    "unserviceable",
    "no rider",
    "no driver",
    "out of service",
    "outside",
    "fleet",
    "serviceability",
)


class NoonSendError(RuntimeError):
    """A call to noon Send that did not do what we asked.

    `is_unserviceable` is the distinction that matters to the caller: a refusal
    to carry this particular parcel means try the other courier, where a 500
    means try again.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_unserviceable(self) -> bool:
        """They can't carry this one — over the distance cap, outside the fleet
        area, or nobody available."""
        if self.status in {400, 404, 409, 422}:
            return True
        text = str(self).lower()
        return any(marker in text for marker in _UNSERVICEABLE_MARKERS)


def coordinate(value: float) -> int:
    """Decimal degrees as noon Send wants them: the degree times 10^7."""
    return round(float(value) * 10_000_000)


def fils(amount: Any) -> int:
    """AED as an integer of 1/100, which is the only money format they take."""
    return round(float(amount) * 100)


@dataclass(frozen=True)
class NoonSendConfig:
    api_key: str
    env: str
    locale: str
    client_code: str
    timeout: float

    @property
    def host(self) -> str:
        return HOSTS.get(self.env, HOSTS["staging"])

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_configured(self) -> bool:
        """Whether we can talk to noon Send at all.

        The key only. Which outlet we collect from is a property of the branch,
        not of the account, so "can we reach them" and "does this branch have
        somewhere to collect from" are two separate questions with two separate
        answers — and the second one is asked per dispatch.
        """
        return bool(self.api_key)


def _config() -> NoonSendConfig:
    return NoonSendConfig(
        api_key=settings.NOON_SEND_API_KEY,
        env=settings.NOON_SEND_ENV,
        locale=settings.NOON_SEND_LOCALE,
        client_code=settings.NOON_SEND_CLIENT_CODE,
        timeout=settings.NOON_SEND_TIMEOUT_SECONDS,
    )


class NoonSendClient:
    """Every noon Send public endpoint we use, one method each."""

    def __init__(self, config: NoonSendConfig | None = None) -> None:
        self._config = config

    @property
    def config(self) -> NoonSendConfig:
        # Read through to settings so a test can patch the environment without
        # having to rebuild the module-level singleton.
        return self._config or _config()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    # ── transport ─────────────────────────────────────────────────────────────

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
        attempts: int = 2,
    ) -> Any:
        config = self.config
        if not config.api_key:
            raise NoonSendError("noon Send is not configured")

        headers = {
            "X-API-Key": config.api_key,
            "X-Locale": config.locale,
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        last: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout or config.timeout
                ) as client:
                    response = await client.request(
                        method,
                        config.host + path,
                        json=json_body,
                        headers=headers,
                    )
            except httpx.HTTPError as exc:
                last = exc
                if attempt + 1 < attempts:
                    continue
                raise NoonSendError(f"noon Send is unreachable: {exc}") from exc

            if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                last = NoonSendError(
                    f"noon Send returned {response.status_code}",
                    status=response.status_code,
                )
                continue

            return _unwrap(response)

        raise NoonSendError(str(last) if last else "noon Send call failed")

    # ── tasks ─────────────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        order_reference: str,
        outlet_code: str,
        drop_off_address: dict[str, Any],
        prepaid_value: int = 0,
        cod_value: int = 0,
        package_count: int = 1,
        tags: list[str] | None = None,
        delivery_notes: str | None = None,
        pickup_notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /public/v1/create-task — a rider is dispatched to the kitchen.

        `outlet_code` is which branch the rider collects from — required, and
        deliberately not defaulted from configuration. A task sent to the wrong
        kitchen is a rider standing outside a closed door, and the caller always
        knows which branch it resolved.

        The idempotency key is what stops a retried request from putting two
        riders on one cake, so it defaults to the order reference rather than
        being left to the caller to remember.
        """
        if not outlet_code:
            raise NoonSendError("This branch is not registered with noon Send")

        config = self.config
        body: dict[str, Any] = {
            "order_reference": order_reference,
            "outlet_code": outlet_code,
            "client_code": config.client_code,
            "drop_off_address": drop_off_address,
            "package_count": package_count,
            "tags": tags or [],
        }
        # Exactly one of the two, and only when non-zero: sending both is a 422
        # even if one of them is a zero.
        if cod_value:
            body["cod_value"] = cod_value
        else:
            body["prepaid_value"] = prepaid_value
        if delivery_notes:
            body["delivery_notes"] = delivery_notes[:250]
        if pickup_notes:
            body["pickup_notes"] = pickup_notes[:250]

        return (
            await self._call(
                "POST",
                "/public/v1/create-task",
                json_body=body,
                idempotency_key=idempotency_key or order_reference,
            )
            or {}
        )

    async def get_task(self, mp_task_nr: str) -> dict[str, Any]:
        """GET /public/v1/tasks/{nr} — status, rider details, live position."""
        return await self._call("GET", f"/public/v1/tasks/{mp_task_nr}") or {}

    async def cancel_task(self, mp_task_nr: str) -> dict[str, Any]:
        """
        POST /public/v1/tasks/{nr}/cancel.

        Only while the parcel is still at the kitchen — `pending_assignment`,
        `assigned` or `arrived_at_pickup_location`. Permanent, and charged on
        production.
        """
        return (
            await self._call(
                "POST",
                f"/public/v1/tasks/{mp_task_nr}/cancel",
                idempotency_key=f"cancel-{mp_task_nr}",
            )
            or {}
        )

    # ── account ───────────────────────────────────────────────────────────────

    async def get_configurations(self) -> dict[str, Any]:
        """
        GET /public/v1/configurations — the limits this partner actually has.

        Distance, COD ceiling, prepaid ceiling and batching, resolved from the
        API key. Worth asking rather than guessing: the integration doc says the
        standard distance limit is 15 km, the rate card prices bands out to 20,
        and the staging partner answers 50 km — three numbers for one question,
        and only this one is about our own account.
        """
        return await self._call("GET", "/public/v1/configurations") or {}

    async def get_outlets(self) -> list[dict[str, Any]]:
        """GET /public/v1/outlets — noon-side outlets we may collect from."""
        result = await self._call("GET", "/public/v1/outlets")
        return result if isinstance(result, list) else []

    # ── pickup points ─────────────────────────────────────────────────────────

    async def check_serviceability(self, latitude: float, longitude: float) -> bool:
        """Whether a rider fleet covers this point at all."""
        result = await self._call(
            "POST",
            "/public/v1/pickup-points/check-serviceability",
            json_body={
                "latitude": coordinate(latitude),
                "longitude": coordinate(longitude),
            },
        )
        return bool((result or {}).get("is_serviceable"))

    async def create_pickup_point(
        self,
        *,
        name: str,
        latitude: float,
        longitude: float,
        address_line: str,
        contact_phone_number: str,
        external_code: str | None = None,
        address_details: str | None = None,
        pickup_instructions: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /public/v1/pickup-points/create — register the kitchen.

        Run once per kitchen, by hand, from
        `scripts/register_noon_send_pickup.py`. The `code` it returns is what
        `branches.noon_send_outlet_code` holds; a branch without one cannot be a
        noon Send pickup.
        """
        body: dict[str, Any] = {
            "name": name,
            "latitude": coordinate(latitude),
            "longitude": coordinate(longitude),
            "address_line": address_line,
            "contact_phone_number": contact_phone_number,
        }
        if external_code:
            body["external_code"] = external_code
        if address_details:
            body["address_details"] = address_details
        if pickup_instructions:
            body["pickup_instructions"] = pickup_instructions
        return (
            await self._call(
                "POST",
                "/public/v1/pickup-points/create",
                json_body=body,
            )
            or {}
        )

    async def list_pickup_points(self) -> list[dict[str, Any]]:
        """GET /public/v1/pickup-points/list."""
        result = await self._call("GET", "/public/v1/pickup-points/list")
        return (result or {}).get("pickup_points") or []


def _unwrap(response: httpx.Response) -> Any:
    """
    The body, or a `NoonSendError` carrying whatever they said went wrong.

    Errors come back in three shapes depending on how deep the request got: a
    FastAPI `detail` string, a `detail` list of field validation objects, or a
    bare message. All three are flattened into one sentence, because the only
    consumer is an admin reading `last_error` on a delivery row.
    """
    if response.status_code == 204 or not response.content:
        if response.is_success:
            return None
        raise NoonSendError(
            f"noon Send returned {response.status_code}", status=response.status_code
        )

    try:
        payload = response.json()
    except ValueError:
        if response.is_success:
            return None
        raise NoonSendError(
            f"noon Send returned {response.status_code} with an unreadable body",
            status=response.status_code,
        ) from None

    if response.is_success:
        # Some endpoints wrap the answer in `data`, some do not.
        if isinstance(payload, dict) and set(payload) == {"data"}:
            return payload["data"]
        return payload

    message = _message(payload) or response.text[:200]
    logger.warning("noon Send %s: %s", response.status_code, message)
    raise NoonSendError(f"noon Send: {message}", status=response.status_code)


def _message(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""

    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            field = ".".join(str(p) for p in (item.get("loc") or []) if p != "body")
            parts.append(
                f"{field}: {item.get('msg')}" if field else str(item.get("msg"))
            )
        return "; ".join(p for p in parts if p)
    return str(payload.get("message") or payload.get("error") or "")


provider = NoonSendClient()
