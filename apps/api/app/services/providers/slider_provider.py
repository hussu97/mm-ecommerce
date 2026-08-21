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
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.config import settings

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
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


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
        idempotency_key: str | None = None,
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
        if config.account_id:
            headers["X-Account-Id"] = config.account_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

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
        drop_off: dict[str, Any],
    ) -> dict[str, Any]:
        """
        POST /deliveries/fare — what each vehicle tier would cost for this run.

        The answer is keyed by vehicle (`bike`, `car`), each block carrying an
        availability flag, a fare and the road distance Slider bills against.
        That distance is the number worth having: it is measured, where ours is
        a straight line times a fitted factor.

        **This is not a coverage check.** It priced Riyadh, Muscat and Liwa
        happily. Treat a fare as a price and nothing more.
        """
        return (
            await self._call(
                "POST",
                "/deliveries/fare",
                json_body={"pickup": pickup, "drop_off": drop_off},
            )
            or {}
        )

    async def create_delivery(
        self,
        *,
        reference: str,
        vehicle: str,
        pickup: dict[str, Any],
        drop_off: dict[str, Any],
        cod_amount: float = 0.0,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /deliveries — a rider is dispatched to the kitchen.

        A 422 here is the only serviceability answer Slider gives, so the caller
        treats `SliderError.is_unserviceable` as "book somebody else" rather
        than as a failure to report.

        The idempotency key defaults to the reference so that a retried request
        after a timeout is the same delivery to them rather than a second rider
        to us.
        """
        body: dict[str, Any] = {
            "reference": reference,
            "vehicle_type": vehicle,
            "pickup": pickup,
            "drop_off": drop_off,
        }
        if cod_amount:
            body["cod_amount"] = cod_amount
        if notes:
            body["notes"] = notes[:250]
        return (
            await self._call(
                "POST",
                "/deliveries",
                json_body=body,
                idempotency_key=idempotency_key or reference,
            )
            or {}
        )

    async def get_delivery(self, delivery_id: str) -> dict[str, Any]:
        """GET /deliveries/{id} — status, rider details, tracking link."""
        return await self._call("GET", f"/deliveries/{delivery_id}") or {}

    async def cancel_delivery(self, delivery_id: str) -> dict[str, Any]:
        """POST /deliveries/{id}/cancel — only before the parcel is collected."""
        return (
            await self._call(
                "POST",
                f"/deliveries/{delivery_id}/cancel",
                idempotency_key=f"cancel-{delivery_id}",
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
