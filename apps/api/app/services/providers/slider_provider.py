"""
Slider delivery API client.

Transport only: hosts, headers, retries and the error shape. What we send and
what we do with the answer lives in `slider_service`, the same split the other
two courier pairs use.

Four things about this API shape everything downstream, and all four were found
by calling it rather than by reading about it.

**Their WAF refuses a request with no `User-Agent`.** A header-less call gets a
403 that looks exactly like a bad key. That is why the proof-of-concept worked
under `curl` and failed under plain Node: curl sends a User-Agent and Node does
not. Every request here names itself.

**A non-JSON body is an error, never a fare.** The same WAF answers with an HTML
challenge page under a 200, and a client that shrugs and moves on parses
`undefined` into a price. Anything that is not JSON raises here.

**There is no serviceability endpoint, and `/deliveries/fare` is not one.** It
priced Riyadh and Muscat in testing, and every one of the 97 UAE areas we asked
about including Liwa at 345 km. The only signal that Slider cannot reach an
address is a 422 when the delivery is actually created, which is why
`SliderError.is_unserviceable` exists and why the caller must fall back rather
than strand a paid order.

**There is no quotation id.** The fare call returns a price and a distance and
nothing to quote back at creation time, so a booking is priced independently of
the estimate that preceded it. `slider_service` computes the vehicle once, in
one function, for exactly this reason.

Authentication is a bearer key. The webhook they POST to us carries a **static
token in a header we choose the name of** — not a signature, so there is no body
to verify and the token is the whole of the check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.money import money_or_none

logger = logging.getLogger(__name__)

__all__ = [
    "SliderClient",
    "SliderConfig",
    "SliderError",
    "aed",
    "provider",
]

#: Their two environments, including the `/v1` prefix every path we call sits
#: under. `_call` concatenates (`config.host + path`) rather than `urljoin`,
#: which is what lets a versioned base carry its prefix — `urljoin` would
#: discard the `/v1` and every call would 404.
#:
#: Confirmed live on 2026-08-21: both answer `POST /v1/deliveries/fare` with a
#: 401 unauthenticated, so the host and the path layout are right. They replace
#: `api.staging.slider.ae` / `api.slider.ae`, which were guessed and never
#: resolved — the whole `slider.ae` zone SERVFAILs, apex included.
#:
#: `SLIDER_ENV` may also be an absolute `http(s)://` origin, which wins over
#: both and remains the fix that does not need a deploy if these ever move.
HOSTS = {
    "staging": "https://api-sandbox.slider-app.com/v1",
    "production": "https://api.slider-app.com/v1",
}

#: Errors worth trying again. A courier that is briefly unreachable should not
#: fail a dispatch, and a 429 is a queue rather than a refusal. Deliberately
#: nothing else: a 4xx from Slider is an answer, and repeating the question does
#: not change it.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Named because their WAF is the reason. A request with no `User-Agent` is
#: answered with a 403 that carries no hint of what was wrong with it.
USER_AGENT = "MeltingMomentsCakes/1.0 (+https://meltingmomentscakes.com)"


class SliderError(RuntimeError):
    """A call to Slider that did not do what we asked.

    `is_unserviceable` is the distinction the caller acts on: "we cannot carry
    this one" means book the other courier, where a 500 means ask again.

    Slider has no coverage endpoint, so a 422 at creation is the *only* way we
    ever learn that an address is outside their area — which makes this property
    load-bearing rather than a nicety. A refusal misread as an outage is a paid,
    packed order with nobody coming for it.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_unserviceable(self) -> bool:
        return self.status in {400, 404, 409, 422}


def aed(value: Any) -> Decimal | None:
    """A money field of theirs as AED, or None if it is not a number.

    Their fare blocks have used `fare`, `total` and `amount` for the same
    figure across the endpoints we have seen, so the caller tries several keys
    and this only has to answer "is this one a number".
    """
    return money_or_none(value)


@dataclass(frozen=True)
class SliderConfig:
    api_key: str
    account_id: str
    env: str
    timeout: float

    @property
    def host(self) -> str:
        env = (self.env or "").strip()
        if env.startswith("http://") or env.startswith("https://"):
            return env.rstrip("/")
        return HOSTS.get(env, HOSTS["staging"])

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_configured(self) -> bool:
        """Whether we can talk to Slider at all.

        The key only. An empty one means a `slider` zone still prices and sells
        exactly as it does today and simply falls back to another courier — the
        same contract Lalamove and noon Send already have, so a missing
        credential is a fallback and never an outage.
        """
        return bool(self.api_key)


def _config() -> SliderConfig:
    return SliderConfig(
        api_key=settings.SLIDER_API_KEY,
        account_id=settings.SLIDER_ACCOUNT_ID,
        env=settings.SLIDER_ENV,
        timeout=settings.SLIDER_TIMEOUT_SECONDS,
    )


class SliderClient:
    """Every Slider endpoint we use, one method each."""

    def __init__(self, config: SliderConfig | None = None) -> None:
        self._config = config

    @property
    def config(self) -> SliderConfig:
        # Read through to settings so a test can patch the environment without
        # rebuilding the module-level singleton.
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
        timeout: float | None = None,
        attempts: int = 2,
    ) -> Any:
        config = self.config
        if not config.api_key:
            raise SliderError("Slider is not configured")

        headers = {
            # `X-Slider-Key`, not `Authorization: Bearer`. Verified against the
            # sandbox on 2026-08-21: a Bearer token is ignored outright — the
            # reply to it is byte-identical to sending no credentials at all
            # ("Unauthorized: X-Slider-Key header is required"), where a wrong
            # value in this header earns a different error ("Invalid
            # X-Slider-Key"). A scheme they ignore fails as an auth error that
            # reads like a bad key, so it is worth naming here.
            "X-Slider-Key": config.api_key,
            "Accept": "application/json",
            # Not decoration. See the module docstring: without this their WAF
            # answers 403 and the failure reads as a rejected key.
            "User-Agent": USER_AGENT,
        }
        # No `X-Account-Id`, and no `Idempotency-Key`. Neither appears in their
        # reference, and the account header is provably ignored: with it set and
        # `account_id` absent from the body, `/deliveries/fare` still answers
        # "account_id or order_number is required". The account travels in the
        # body on every endpoint that wants it, and `order_id` is what makes a
        # repeated create the same delivery to them.

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
                raise SliderError(f"Slider is unreachable: {exc}") from exc

            if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                last = SliderError(
                    f"Slider returned {response.status_code}",
                    status=response.status_code,
                )
                continue

            return _unwrap(response)

        raise SliderError(str(last) if last else "Slider call failed")

    # ── deliveries ────────────────────────────────────────────────────────────

    async def fare(
        self,
        *,
        pickup: dict[str, Any],
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        """
        POST /deliveries/fare — what each vehicle tier would cost for this run.

        Their reference names the drop `delivery`, not `drop_off`, and wants
        `account_id` in the body. Both were wrong here and both failed loudly
        rather than silently, which is the only mercy in it: the drop under any
        other key answers "The delivery field is required".

        The answer is `{distance_km, duration_minutes, vehicles: [...]}`, and
        `vehicles` is a **list** of `{vehicle_type, is_available,
        unavailable_reason, delivery_fee}` — not a mapping keyed by vehicle.
        `distance_km` is top level and belongs to the run, not to a tier. That
        distance is the number worth having: it is measured, where ours is a
        straight line times a fitted factor.

        **This is not a coverage check.** It priced Riyadh, Muscat and Liwa
        happily. Treat a fare as a price and nothing more.
        """
        return (
            await self._call(
                "POST",
                "/deliveries/fare",
                json_body={
                    "account_id": self.config.account_id,
                    "pickup": pickup,
                    "delivery": delivery,
                },
            )
            or {}
        )

    async def create_delivery(
        self,
        *,
        order_id: str,
        vehicle: str,
        pickup: dict[str, Any],
        dropoff: dict[str, Any],
        display_order_id: str | None = None,
        cod_amount: float = 0.0,
        cod_type: str = "cash",
        driver_tip: float = 0.0,
        schedule_at: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /deliveries — a rider is dispatched to the kitchen.

        A 422 here is the only serviceability answer Slider gives, so the caller
        treats `SliderError.is_unserviceable` as "book somebody else" rather
        than as a failure to report.

        `order_id` is ours and is what makes a retry after a timeout the same
        delivery to them rather than a second rider to us — there is no
        idempotency header in their reference, so this field is the whole of it.
        `display_order_id` is what the rider is shown.

        Note `dropoff`, one word: `drop_off` is silently not the field they
        read. Cash on delivery is `payment_on_delivery: {type, amount}` rather
        than a scalar, and their ceilings are AED 350 cash / AED 500 card —
        exceeding one is a 422, which `is_unserviceable` would otherwise read as
        "out of area" and quietly hand to another courier.
        """
        body: dict[str, Any] = {
            "order_id": order_id,
            "account_id": self.config.account_id,
            "vehicle_type": vehicle,
            "pickup": pickup,
            "dropoff": dropoff,
        }
        if display_order_id:
            body["display_order_id"] = display_order_id
        if cod_amount:
            body["payment_on_delivery"] = {
                "type": cod_type,
                "amount": round(float(cod_amount), 2),
            }
        if driver_tip:
            body["driver_tip"] = round(float(driver_tip), 2)
        if schedule_at:
            body["schedule_at"] = schedule_at
        return await self._call("POST", "/deliveries", json_body=body) or {}

    async def get_delivery(self, order_number: str) -> dict[str, Any]:
        """GET /deliveries/{order_number} — status, rider, tracking link.

        Keyed by **their** `order_number` from the create response, not by ours
        and not by an `id` field, which they do not return.
        """
        return await self._call("GET", f"/deliveries/{order_number}") or {}

    async def cancel_delivery(self, order_number: str) -> dict[str, Any]:
        """DELETE /deliveries/{order_number} — before the parcel is collected."""
        return (
            await self._call(
                "DELETE",
                f"/deliveries/{order_number}",
            )
            or {}
        )


def _unwrap(response: httpx.Response) -> Any:
    """
    The body, or a `SliderError` carrying whatever they said went wrong.

    **A body that is not JSON is an error even under a 200.** Their WAF serves
    an HTML challenge page with a success status, and the alternative reading —
    shrug and return None — puts `undefined` where a fare should be and books a
    delivery at a price nobody quoted.
    """
    if response.status_code == 204 or not response.content:
        if response.is_success:
            return None
        raise SliderError(
            f"Slider returned {response.status_code}", status=response.status_code
        )

    try:
        payload = response.json()
    except ValueError:
        raise SliderError(
            f"Slider returned {response.status_code} with a body that is not JSON "
            f"({response.headers.get('content-type', 'no content-type')})",
            status=response.status_code,
        ) from None

    if response.is_success:
        # Some endpoints wrap the answer in `data`, some do not.
        if isinstance(payload, dict) and set(payload) == {"data"}:
            return payload["data"]
        return payload

    message = _message(payload) or response.text[:200]
    logger.warning("Slider %s: %s", response.status_code, message)
    raise SliderError(f"Slider: {message}", status=response.status_code)


def _message(payload: Any) -> str:
    """Whatever they called the problem, flattened into one sentence.

    The only consumer is an admin reading `last_error` on a delivery row, so a
    list of field errors becomes a semicolon-separated line rather than a shape
    somebody has to expand.
    """
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""

    for key in ("message", "error", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            parts = [
                str(item.get("msg") if isinstance(item, dict) else item)
                for item in value
            ]
            joined = "; ".join(p for p in parts if p and p != "None")
            if joined:
                return joined

    errors = payload.get("errors")
    if isinstance(errors, dict):
        return "; ".join(
            f"{field}: {', '.join(map(str, msgs)) if isinstance(msgs, list) else msgs}"
            for field, msgs in errors.items()
        )
    return ""


provider = SliderClient()
