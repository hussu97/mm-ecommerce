"""
Lalamove API v3 client.

Transport only: signing, headers, retries and error shape. What we send and
what we do with the answer lives in `lalamove_service`, so the mapping between
an order and a courier booking is readable without wading through HMAC.

Authentication is an HMAC-SHA256 over a four-part string:

    <timestamp>\\r\\n<METHOD>\\r\\n<path>\\r\\n\\r\\n<body>

signed with the API secret and sent as `Authorization: hmac <key>:<ts>:<sig>`.
Webhooks arriving from Lalamove are signed the same way, except the "body" is
the `data` object of the payload rather than the whole thing, and the path is
our own receiving path.

UAE is live on production despite being absent from the public market table:
`GET /v3/cities` with `Market: AE` returns AE AUH, AE DXB and AE SHJ, and the
quotation schema requires `language` to be exactly `en_AE`. The sandbox is a
different story — its AE pricing engine is not provisioned and returns
`500 ERR_UNKNOWN`, and its wallet is unfunded, so sandbox work has to be done
in another market (HK) and validated against production AE.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "LalamoveError",
    "LalamoveClient",
    "provider",
    "sign",
]

HOSTS = {
    "sandbox": "https://rest.sandbox.lalamove.com",
    "production": "https://rest.lalamove.com",
}

#: Errors worth trying again — a courier that is briefly unreachable should not
#: fail a checkout, and a 429 is a queue, not a refusal.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class LalamoveError(RuntimeError):
    """A call to Lalamove that did not do what we asked.

    Carries the machine-readable id (`ERR_OUT_OF_SERVICE_AREA`,
    `ERR_INSUFFICIENT_CREDIT`, …) because the caller's decision — refuse the
    address, top up the wallet, retry later — depends on which one it is.
    """

    def __init__(
        self, message: str, *, status: int | None = None, error_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_id = error_id

    @property
    def is_out_of_service_area(self) -> bool:
        return self.error_id == "ERR_OUT_OF_SERVICE_AREA"

    @property
    def is_insufficient_credit(self) -> bool:
        return self.error_id == "ERR_INSUFFICIENT_CREDIT"


def sign(secret: str, *, timestamp: str, method: str, path: str, body: str) -> str:
    """The HMAC Lalamove expects, for both outbound calls and inbound webhooks."""
    raw = f"{timestamp}\r\n{method}\r\n{path}\r\n\r\n{body}"
    return hmac.new(
        secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class LalamoveConfig:
    key: str
    secret: str
    env: str
    market: str
    language: str
    service_type: str
    webhook_path: str

    @property
    def host(self) -> str:
        return HOSTS.get(self.env, HOSTS["production"])

    @property
    def is_configured(self) -> bool:
        return bool(self.key and self.secret)


def _config() -> LalamoveConfig:
    return LalamoveConfig(
        key=settings.LALAMOVE_API_KEY,
        secret=settings.LALAMOVE_API_SECRET,
        env=settings.LALAMOVE_ENV,
        market=settings.LALAMOVE_MARKET,
        language=settings.LALAMOVE_LANGUAGE,
        service_type=settings.LALAMOVE_SERVICE_TYPE,
        webhook_path=settings.LALAMOVE_WEBHOOK_PATH,
    )


class LalamoveClient:
    """Every Lalamove v3 endpoint, one method each."""

    def __init__(self, config: LalamoveConfig | None = None) -> None:
        self._config = config

    @property
    def config(self) -> LalamoveConfig:
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
        data: dict[str, Any] | None = None,
        timeout: float | None = None,
        attempts: int = 2,
    ) -> dict[str, Any] | None:
        config = self.config
        if not config.is_configured:
            raise LalamoveError("Lalamove is not configured")

        # The signature covers the exact bytes on the wire, so the body is
        # serialised once and reused rather than left to httpx.
        body = json.dumps({"data": data}, separators=(",", ":")) if data else ""
        last: Exception | None = None

        for attempt in range(attempts):
            timestamp = str(int(time.time() * 1000))
            headers = {
                "Authorization": (
                    f"hmac {config.key}:{timestamp}:"
                    f"{sign(config.secret, timestamp=timestamp, method=method, path=path, body=body)}"
                ),
                "Accept": "application/json",
                "Market": config.market,
                # A fresh id per attempt: Lalamove treats a repeat as the same
                # request, which is what we want for a retry after a timeout.
                "Request-ID": str(uuid.uuid4()),
            }
            if body:
                headers["Content-Type"] = "application/json"

            try:
                async with httpx.AsyncClient(
                    timeout=timeout or settings.LALAMOVE_TIMEOUT_SECONDS
                ) as client:
                    response = await client.request(
                        method,
                        config.host + path,
                        content=body.encode("utf-8") if body else None,
                        headers=headers,
                    )
            except httpx.HTTPError as exc:
                last = exc
                if attempt + 1 < attempts:
                    continue
                raise LalamoveError(f"Lalamove is unreachable: {exc}") from exc

            if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                last = LalamoveError(
                    f"Lalamove returned {response.status_code}",
                    status=response.status_code,
                )
                continue

            return _unwrap(response)

        raise LalamoveError(str(last) if last else "Lalamove call failed")

    # ── quotations ────────────────────────────────────────────────────────────

    async def create_quotation(
        self,
        stops: list[dict[str, Any]],
        *,
        service_type: str | None = None,
        special_requests: list[str] | None = None,
        schedule_at: str | None = None,
        is_route_optimized: bool | None = None,
        item: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /v3/quotations — a price and a `quotationId`, valid five minutes."""
        data: dict[str, Any] = {
            "serviceType": service_type or self.config.service_type,
            "language": self.config.language,
            "stops": stops,
        }
        if special_requests:
            data["specialRequests"] = special_requests
        if schedule_at:
            data["scheduleAt"] = schedule_at
        if is_route_optimized is not None:
            data["isRouteOptimized"] = is_route_optimized
        if item:
            data["item"] = item
        return (
            await self._call("POST", "/v3/quotations", data=data, timeout=timeout) or {}
        )

    async def get_quotation(self, quotation_id: str) -> dict[str, Any]:
        """GET /v3/quotations/{id}"""
        return await self._call("GET", f"/v3/quotations/{quotation_id}") or {}

    # ── orders ────────────────────────────────────────────────────────────────

    async def place_order(
        self,
        *,
        quotation_id: str,
        sender: dict[str, Any],
        recipients: list[dict[str, Any]],
        is_pod_enabled: bool = True,
        metadata: dict[str, str] | None = None,
        partner: str | None = None,
    ) -> dict[str, Any]:
        """POST /v3/orders — debits the prepaid wallet immediately."""
        data: dict[str, Any] = {
            "quotationId": quotation_id,
            "sender": sender,
            "recipients": recipients,
            "isPODEnabled": is_pod_enabled,
        }
        if metadata:
            data["metadata"] = metadata
        if partner:
            data["partner"] = partner
        return await self._call("POST", "/v3/orders", data=data) or {}

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """GET /v3/orders/{id}"""
        return await self._call("GET", f"/v3/orders/{order_id}") or {}

    async def edit_order(
        self, order_id: str, *, stops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """PATCH /v3/orders/{id} — drop-offs only, once, before pickup."""
        return (
            await self._call("PATCH", f"/v3/orders/{order_id}", data={"stops": stops})
            or {}
        )

    async def cancel_order(self, order_id: str) -> None:
        """DELETE /v3/orders/{id} — free while ASSIGNING_DRIVER."""
        await self._call("DELETE", f"/v3/orders/{order_id}")

    async def add_priority_fee(self, order_id: str, fee: str) -> dict[str, Any]:
        """POST /v3/orders/{id}/priority-fee — a tip to speed up matching."""
        return (
            await self._call(
                "POST", f"/v3/orders/{order_id}/priority-fee", data={"priorityFee": fee}
            )
            or {}
        )

    # ── drivers ───────────────────────────────────────────────────────────────

    async def get_driver(self, order_id: str, driver_id: str) -> dict[str, Any]:
        """GET /v3/orders/{id}/drivers/{driverId} — name, phone, live position."""
        return (
            await self._call("GET", f"/v3/orders/{order_id}/drivers/{driver_id}") or {}
        )

    async def change_driver(self, order_id: str, driver_id: str, reason: str) -> None:
        """DELETE /v3/orders/{id}/drivers/{driverId} — after 15 min, before pickup."""
        await self._call(
            "DELETE",
            f"/v3/orders/{order_id}/drivers/{driver_id}",
            data={"reason": reason},
        )

    # ── account ───────────────────────────────────────────────────────────────

    async def get_cities(self) -> dict[str, Any]:
        """GET /v3/cities — the service and vehicle catalogue for our market."""
        return await self._call("GET", "/v3/cities") or {}

    async def set_webhook(self, url: str) -> dict[str, Any]:
        """PATCH /v3/webhook — where Lalamove should push status changes."""
        return await self._call("PATCH", "/v3/webhook", data={"url": url}) or {}

    # ── inbound ───────────────────────────────────────────────────────────────

    def verify_webhook(self, raw_body: bytes) -> dict[str, Any]:
        """
        Check that a webhook really came from Lalamove, and return the payload.

        The signature covers `data` as Lalamove serialised it, so the exact
        bytes are lifted back out of the request rather than re-encoded from
        the parsed object — a re-encode would differ on key order or spacing
        and reject a genuine call.
        """
        config = self.config
        if not config.is_configured:
            raise LalamoveError("Lalamove is not configured")

        try:
            payload = json.loads(raw_body)
        except ValueError as exc:
            raise LalamoveError("Webhook body is not JSON") from exc
        if not isinstance(payload, dict):
            raise LalamoveError("Webhook body is not an object")

        if payload.get("apiKey") != config.key:
            raise LalamoveError("Webhook is for a different API key")

        signature = payload.get("signature")
        if not signature:
            raise LalamoveError("Webhook is unsigned")

        text = raw_body.decode("utf-8", errors="strict")
        timestamp = _raw_value(text, "timestamp") or str(payload.get("timestamp", ""))
        bodies = [
            body
            for body in (
                _raw_value(text, "data"),
                json.dumps(payload.get("data"), separators=(",", ":")),
            )
            if body
        ]
        if not any(
            hmac.compare_digest(
                signature,
                sign(
                    config.secret,
                    timestamp=timestamp,
                    method="POST",
                    path=config.webhook_path,
                    body=body,
                ),
            )
            for body in bodies
        ):
            raise LalamoveError("Webhook signature does not match")

        return payload


def _unwrap(response: httpx.Response) -> dict[str, Any] | None:
    """Lalamove wraps success in `data` and failure in `errors`."""
    if response.status_code == 204 or not response.content:
        if response.is_success:
            return None
        raise LalamoveError(
            f"Lalamove returned {response.status_code}", status=response.status_code
        )

    try:
        payload = response.json()
    except ValueError:
        raise LalamoveError(
            f"Lalamove returned {response.status_code} with an unreadable body",
            status=response.status_code,
        ) from None

    if response.is_success:
        return payload.get("data", payload)

    errors = payload.get("errors") or []
    first = errors[0] if errors else {}
    error_id = first.get("id")
    message = first.get("message") or first.get("detail") or response.text[:200]
    logger.warning("Lalamove %s: %s %s", response.status_code, error_id or "-", message)
    raise LalamoveError(
        f"Lalamove: {message}", status=response.status_code, error_id=error_id
    )


def _raw_value(text: str, key: str) -> str | None:
    """
    The verbatim JSON source of a top-level value, whitespace and all.

    Only used to reproduce the bytes Lalamove signed. Returns None when the key
    is missing or the value runs off the end, and the caller falls back to a
    re-encode.
    """
    marker = f'"{key}"'
    start = text.find(marker)
    if start == -1:
        return None
    cursor = text.find(":", start + len(marker))
    if cursor == -1:
        return None
    cursor += 1
    while cursor < len(text) and text[cursor] in " \t\r\n":
        cursor += 1
    if cursor >= len(text):
        return None

    opening = text[cursor]
    if opening not in "{[":
        end = cursor
        in_string = opening == '"'
        if in_string:
            end += 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    return text[cursor : end + 1]
                end += 1
            return None
        while end < len(text) and text[end] not in ",}\n\r \t":
            end += 1
        return text[cursor:end] or None

    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(cursor, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[cursor : index + 1]
    return None


provider = LalamoveClient()
