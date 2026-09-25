"""
Paymob (UAE) — the third card gateway, and the one that trusts least by default.

Paymob is here for the same reason Ziina is: a Stripe incident should be a row
in `payment_gateways`, not a deploy. It ships built, wired and switched off (see
`is_configured`).

Five things about their API shape everything below, and each one is a place a
port of the Stripe provider would have been quietly and expensively wrong.

**The signature covers twenty fields and nothing else.** A processed callback is
signed as HMAC-SHA512 over a fixed, ordered list of fields in `obj` — amount,
currency, the flags, the transaction and Paymob order ids — passed as `?hmac=`
on the URL. Everything else in the body is unsigned: `extras` (our metadata),
`merchant_order_id`, `is_live`, `refunded_amount_cents`, `data.*`. Worse, the
*same* signed set rides on the customer's own browser redirect, so anyone who
paid for one order holds a valid signature they can replay. So nothing here is
ever read from an unsigned field when it decides anything: the order is found
through the signed Paymob order id (`session_id`), never through `extras`; a
test transaction is told from a live one by the signed `integration_id`, never
by `is_live`; and a refunded amount is re-read from Paymob before it is believed
(`verify_event`).

**Order ids and transaction ids are both bare integers.** Stored raw, one
order's transaction id can equal another order's Paymob order id, and the
handle lookup would bind a payment to the wrong order. They are namespaced —
`ord_<id>` and `txn_<id>` — everywhere this application stores them, and the
prefix is stripped only on the way back out to Paymob.

**One intention can carry many transactions.** A declined card on the hosted
page can be retried on the same page, which is a new transaction on the same
Paymob order. So a FAILED event deliberately carries no `payment_id`: adopting
the declined transaction's id onto the attempt would make every later refund
aim at the transaction that never took any money.

**Refunds take no idempotency key.** Stripe takes a header and Ziina a
client-chosen id; Paymob takes neither, so the retry-safety invariant in
`payment_service.refund_order` has nothing on the far side to dedupe against.
`refund` supplies it itself by reading the transaction back first — see there.

**The callback retry policy is undocumented.** Paymob says only that callbacks
"must support retries". `fetch_outcome` is the answer: a transaction inquiry
that the reconcile sweep uses to learn about a payment whose webhook never came.

Amounts are in fils. `10000` is AED 100.00.

Docs: https://developers.paymob.com/paymob-docs (UAE host: uae.paymob.com)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.core.money import money
from app.models.order import Order
from app.services.providers.base import (
    GatewayEvent,
    GatewayRefund,
    GatewaySession,
    GatewayUnavailableError,
    PaymentEventType,
    PaymentFailureReason,
    PaymentGatewayProvider,
)

logger = logging.getLogger(__name__)

__all__ = ["PaymobProvider", "provider"]

#: The floor the `payment_gateways` row falls back to. Paymob does not publish
#: one; this matches the other two, so an order is never routable to one card
#: gateway and refused by the next for being too small.
_AED_MINIMUM = Decimal("2.00")

#: Answers that mean "try again, or try someone else" rather than "the order is
#: wrong". A 4xx is about what we sent; these are about Paymob.
_UNAVAILABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Answers to creating an intention that mean *our* configuration is broken —
#: a bad or rotated key (401/403), an integration id Paymob no longer knows
#: (404). Failed over like an outage; a 400/406 is about the order, and is not.
_MISCONFIGURED_STATUSES = frozenset({401, 403, 404})

#: The processed-callback signature, in Paymob's order. `order.id` is spelled
#: `order` (or `order_id`) on the redirect's query string, where every field is
#: flat — see `_signed_value`.
#: https://developers.paymob.com/paymob-docs/developers/webhook-callbacks-and-hmac/hmac/hmac-transaction-callback
_TRANSACTION_HMAC_FIELDS = (
    "amount_cents",
    "created_at",
    "currency",
    "error_occured",
    "has_parent_transaction",
    "id",
    "integration_id",
    "is_3d_secure",
    "is_auth",
    "is_capture",
    "is_refunded",
    "is_standalone_payment",
    "is_voided",
    "order.id",
    "owner",
    "pending",
    "source_data.pan",
    "source_data.sub_type",
    "source_data.type",
    "success",
)

#: A saved-card token callback is signed over a different set. We never ask for
#: tokens, but a merchant account can be configured to issue them, and a push we
#: cannot verify is a 400 Paymob will keep retrying.
_TOKEN_HMAC_FIELDS = (
    "card_subtype",
    "created_at",
    "email",
    "id",
    "masked_pan",
    "merchant_id",
    "order_id",
    "token",
)

#: The intention statuses whose hosted page can still take a payment. Only these
#: are worth re-offering in an abandoned-cart reminder.
_RESUMABLE_INTENTION_STATUSES = frozenset({"intended"})

_ORDER_PREFIX = "ord_"
_TXN_PREFIX = "txn_"

#: ISO-8583 acquirer response codes → the buckets the customer is shown.
#:
#: Provisional, and labelled so on purpose. Paymob publishes no decline
#: taxonomy; `acq_response_code` is the acquirer's own two-character answer,
#: which on the card networks is ISO-8583. These are the standard meanings.
#: Real sandbox and live traffic is what confirms or corrects them (the
#: go-live checklist in PRODUCTION.md says so), and until then the raw code and
#: message are always kept alongside the bucket.
_ACQ_FAILURE_REASONS: dict[str, PaymentFailureReason] = {
    "51": PaymentFailureReason.INSUFFICIENT_FUNDS,
    "61": PaymentFailureReason.INSUFFICIENT_FUNDS,
    "65": PaymentFailureReason.INSUFFICIENT_FUNDS,
    "54": PaymentFailureReason.EXPIRED_CARD,
    "33": PaymentFailureReason.EXPIRED_CARD,
    "82": PaymentFailureReason.INCORRECT_CVC,
    "N7": PaymentFailureReason.INCORRECT_CVC,
    "14": PaymentFailureReason.INCORRECT_NUMBER,
    "57": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "58": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "62": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "19": PaymentFailureReason.PROCESSING_ERROR,
    "91": PaymentFailureReason.PROCESSING_ERROR,
    "96": PaymentFailureReason.PROCESSING_ERROR,
    "94": PaymentFailureReason.DUPLICATE,
    "05": PaymentFailureReason.CARD_DECLINED,
    "41": PaymentFailureReason.CARD_DECLINED,
    "43": PaymentFailureReason.CARD_DECLINED,
    "59": PaymentFailureReason.CARD_DECLINED,
}

#: The Bearer token transaction reads need. Minted from `PAYMOB_API_KEY`, good
#: for an hour on Paymob's side; kept a little under that here.
_TOKEN_TTL_SECONDS = 50 * 60
_bearer: dict[str, Any] = {"token": None, "expires": 0.0}


# ── small pure helpers ─────────────────────────────────────────────────────────


def _wrap(prefix: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    return f"{prefix}{value}"


def _unwrap(prefix: str, value: str | None) -> str:
    if not value or not value.startswith(prefix):
        raise BadRequestError(f"Not a Paymob handle: {value!r}")
    return value[len(prefix) :]


def _as_int(value: Any) -> int | None:
    """Minor units, or nothing. Never a partially-parsed number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(Decimal(str(value)))
    except (ArithmeticError, TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    """A flag from either form: JSON `true`, or the redirect's `"true"`."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _hmac_text(value: Any) -> str:
    """
    One field as Paymob concatenates it.

    Booleans are lowercase words (`str(True)` is `"True"`, which would never
    match). A null is empty — the documented example never shows one, which is
    why a null-PAN payment (a wallet) is on the go-live checklist. Numbers are
    left exactly as they arrived: the body is parsed with `parse_float=Decimal`
    so `100.0` is not quietly rewritten into a different string.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _dig(obj: Mapping[str, Any], dotted: str) -> Any:
    node: Any = obj
    for part in dotted.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def _signed_value(fields: Mapping[str, Any], name: str, *, flat: bool) -> Any:
    """
    A signed field from the JSON `obj` (nested) or the redirect query (flat).

    On the redirect every key is flat — `source_data.pan` is literally that key
    — and the Paymob order id is `order` in their sample and `order_id` in their
    table, so both are accepted.
    """
    if not flat:
        if name == "order.id" and not isinstance(fields.get("order"), Mapping):
            # The read endpoints sometimes answer with the order as a bare id
            # rather than the object a callback carries.
            return fields.get("order")
        return _dig(fields, name)
    if name == "order.id":
        return fields.get("order", fields.get("order_id"))
    return fields.get(name)


#: A canonical integer: no sign, no leading zero — a zero moved off the front
#: of one field is exactly what a re-split leaves behind.
_DIGITS = re.compile(r"^(0|[1-9]\d*)$")
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?$"
)
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_FLAGS = (
    "error_occured",
    "has_parent_transaction",
    "is_3d_secure",
    "is_auth",
    "is_capture",
    "is_refunded",
    "is_standalone_payment",
    "is_voided",
    "pending",
    "success",
)


def _assert_signed_shape(fields: Mapping[str, Any], *, flat: bool) -> None:
    """
    Every signed field has exactly the shape Paymob gives it.

    Paymob's signature is over the fields *concatenated with no separator*, so
    it proves the string, not where one field ends and the next begins: a
    holder of one valid signed set (anyone who paid for one order) can move a
    digit from `order.id` into `owner`, or from `amount_cents` into
    `created_at`, and the signature still verifies. The moved values are
    malformed — an id with a leading dash, a timestamp missing its year — so
    requiring each field's own shape closes the gap without breaking the
    scheme we have to interoperate with.
    """

    def value(name: str) -> str:
        return _hmac_text(_signed_value(fields, name, flat=flat))

    for name in ("amount_cents", "id", "integration_id", "order.id", "owner"):
        if not _DIGITS.match(value(name)):
            raise BadRequestError(
                f"Malformed signed field {name!r} — signature not trusted"
            )
    if not _TIMESTAMP.match(value("created_at")):
        raise BadRequestError(
            "Malformed signed field 'created_at' — signature not trusted"
        )
    if not _CURRENCY.match(value("currency")):
        raise BadRequestError(
            "Malformed signed field 'currency' — signature not trusted"
        )
    for name in _FLAGS:
        if value(name) not in ("true", "false"):
            raise BadRequestError(
                f"Malformed signed field {name!r} — signature not trusted"
            )


def _expected_hmac(values: list[Any], secret: str) -> str:
    message = "".join(_hmac_text(v) for v in values)
    return hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha512
    ).hexdigest()


def _error_message(response: httpx.Response) -> str:
    """Whatever Paymob said went wrong, short enough to log and show."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            if body.get(key):
                return str(body[key])[:200]
    return f"HTTP {response.status_code}"


def _failure_reason(
    acq_code: str | None,
    txn_code: str | None,
    auth_status: str | None,
    error_occured: bool,
) -> PaymentFailureReason:
    if acq_code and acq_code.strip().upper() in _ACQ_FAILURE_REASONS:
        return _ACQ_FAILURE_REASONS[acq_code.strip().upper()]
    if auth_status and "SUCCESS" not in auth_status.upper():
        return PaymentFailureReason.AUTHENTICATION_REQUIRED
    if txn_code and "AUTHENTICATION" in txn_code.upper():
        return PaymentFailureReason.AUTHENTICATION_REQUIRED
    # Stripe's default for a refusal with no code, so the customer reads the
    # same sentence whichever processor said no. An `error_occured` is the
    # gateway's own fault rather than the card's, and says so.
    return (
        PaymentFailureReason.PROCESSING_ERROR
        if error_occured
        else PaymentFailureReason.CARD_DECLINED
    )


def _split_name(order: Order) -> tuple[str, str]:
    """First and last name, both non-empty — Paymob 400s on either missing."""
    raw = (getattr(order, "customer_name", None) or "").strip()
    if not raw:
        raw = (order.email or "").split("@", 1)[0].strip()
    parts = raw.split(None, 1)
    first = (parts[0] if parts else "") or "Customer"
    last = (parts[1] if len(parts) > 1 else "") or "NA"
    return first[:50], last[:50]


class PaymobProvider(PaymentGatewayProvider):
    """Paymob Unified Checkout + processed callback + refunds."""

    code = "paymob"

    # ── plumbing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _base_url() -> str:
        return settings.PAYMOB_API_URL.rstrip("/")

    @staticmethod
    def _secret_headers() -> dict[str, str]:
        # The literal word `Token`, not `Bearer` — Paymob's own convention for
        # the secret key, and a 401 if it is spelt the usual way.
        return {
            "Authorization": f"Token {settings.PAYMOB_SECRET_KEY}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _integration_ids() -> set[int]:
        return {
            i
            for i in (
                settings.PAYMOB_CARD_INTEGRATION_ID,
                settings.PAYMOB_APPLE_PAY_INTEGRATION_ID,
            )
            if i
        }

    @staticmethod
    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=settings.PAYMOB_TIMEOUT_SECONDS)

    def _checkout_url(self, client_secret: str) -> str:
        query = urlencode(
            {"publicKey": settings.PAYMOB_PUBLIC_KEY, "clientSecret": client_secret}
        )
        return f"{settings.PAYMOB_CHECKOUT_URL.rstrip('/')}/?{query}"

    async def _bearer_token(self) -> str:
        """
        A Bearer for the read endpoints, minted from the API key and reused.

        Transaction inquiry and the transaction GET do not take the secret key;
        they take a token from `POST /api/auth/tokens`. Minted lazily and kept
        for most of its hour so a reconcile sweep does not authenticate once per
        order.
        """
        now = time.monotonic()
        if _bearer["token"] and _bearer["expires"] > now:
            return _bearer["token"]
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/api/auth/tokens",
                    json={"api_key": settings.PAYMOB_API_KEY},
                )
        except httpx.HTTPError as exc:
            raise GatewayUnavailableError(f"Paymob unreachable: {exc}") from exc
        if response.status_code in _UNAVAILABLE_STATUSES:
            raise GatewayUnavailableError(f"Paymob auth error {response.status_code}")
        if response.status_code >= 400:
            raise BadRequestError(
                f"Paymob refused the API key: {_error_message(response)}"
            )
        token = (response.json() or {}).get("token")
        if not token:
            raise GatewayUnavailableError("Paymob auth returned no token")
        _bearer["token"] = token
        _bearer["expires"] = now + _TOKEN_TTL_SECONDS
        return token

    async def _read(self, method: str, path: str, **kwargs) -> dict:
        """An authenticated read. Gateway faults are `GatewayUnavailableError`."""
        token = await self._bearer_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with self._client() as client:
                response = await client.request(
                    method, f"{self._base_url()}{path}", headers=headers, **kwargs
                )
        except httpx.HTTPError as exc:
            raise GatewayUnavailableError(f"Paymob unreachable: {exc}") from exc
        if response.status_code == 401:
            # A token Paymob has retired early. Forget it so the next call
            # mints a fresh one, and let this one be retried by its caller.
            _bearer["token"] = None
            raise GatewayUnavailableError("Paymob rejected the cached token")
        if response.status_code in _UNAVAILABLE_STATUSES:
            raise GatewayUnavailableError(
                f"Paymob error {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise BadRequestError(f"Paymob refused {path}: {_error_message(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayUnavailableError("Paymob returned non-JSON") from exc
        if not isinstance(body, dict):
            raise GatewayUnavailableError("Paymob returned a non-object")
        return body

    async def _get_transaction(self, txn_id: str) -> dict:
        return await self._read("GET", f"/api/acceptance/transactions/{txn_id}")

    # ── PaymentGatewayProvider interface ──────────────────────────────────────

    def is_configured(self) -> bool:
        """
        Whether this environment may charge a card on Paymob.

        `PAYMOB_ENABLED` is separate from the keys on purpose, exactly as
        `ZIINA_ENABLED` is: keys arrive on a VM for many reasons and none of them
        is a decision to route live cards through a processor nobody has signed
        off. Every credential the money path needs is required too — a gateway
        that can charge but cannot verify the webhook telling it so would take
        money and leave the order at `created`.
        """
        return bool(
            settings.PAYMOB_ENABLED
            and settings.PAYMOB_SECRET_KEY
            and settings.PAYMOB_PUBLIC_KEY
            and settings.PAYMOB_API_KEY
            and settings.PAYMOB_HMAC_SECRET
            and settings.PAYMOB_CARD_INTEGRATION_ID
            and settings.PAYMOB_CALLBACK_BASE_URL
        )

    def minimum_amount(self) -> Decimal | None:
        return _AED_MINIMUM

    async def create_session(
        self, order: Order, *, test_mode: bool = False
    ) -> GatewaySession:
        """
        Create a Paymob intention for the hosted Unified Checkout page.

        `test_mode` is ignored, as it is for Stripe: Paymob decides test versus
        live from the key and the integration id, not from a flag on the call.
        """
        session, _ = await self._create_intention(
            order, payment_methods=[settings.PAYMOB_CARD_INTEGRATION_ID]
        )
        return session

    async def create_apple_pay_intention(
        self, order: Order
    ) -> tuple[GatewaySession, str]:
        """
        An intention offering only Apple Pay, for the in-page Pixel button.

        Returns the session (recorded as an attempt exactly like a hosted one,
        so the webhook finds it by its Paymob order id) and the client secret
        the browser mounts Pixel with.
        """
        if not settings.PAYMOB_APPLE_PAY_INTEGRATION_ID:
            raise BadRequestError("Apple Pay is not configured for Paymob here")
        return await self._create_intention(
            order,
            payment_methods=[settings.PAYMOB_APPLE_PAY_INTEGRATION_ID],
            hosted=False,
        )

    async def _create_intention(
        self, order: Order, *, payment_methods: list[int], hosted: bool = True
    ) -> tuple[GatewaySession, str]:
        if not self.is_configured():
            # Unreachable through the router, which asks first. An unconfigured
            # gateway must still not produce a bare 401 mid-checkout.
            raise BadRequestError("Paymob is not configured in this environment")

        total = money(order.total)
        amount_cents = int(total * 100)
        first, last = _split_name(order)
        callback_base = settings.PAYMOB_CALLBACK_BASE_URL.rstrip("/")

        # One line, not one per product. The intention's `amount` must equal the
        # sum of its items or Paymob answers 406, and whether that sum is of unit
        # prices or line totals is not documented — a guess wrong there fails
        # every discounted or multi-quantity checkout outright. One line carrying
        # the whole total cannot disagree with it, and the contents still reach
        # the hosted page as the description.
        summary = ", ".join(
            f"{item.quantity}× {item.product_name}" for item in (order.items or [])
        )
        payload: dict[str, Any] = {
            "amount": amount_cents,
            "currency": "AED",
            "payment_methods": payment_methods,
            "items": [
                {
                    "name": f"Melting Moments · {order.order_number}"[:50],
                    "amount": amount_cents,
                    "quantity": 1,
                    "description": (summary or order.order_number)[:255],
                }
            ],
            "billing_data": {
                "first_name": first,
                "last_name": last,
                "email": order.email or "customer@meltingmomentscakes.com",
                "phone_number": (
                    getattr(order, "customer_phone", None) or "+971500000000"
                ),
                # The rest are optional in the current docs and mandatory in the
                # older ones; "NA" is what Paymob's own examples send.
                "apartment": "NA",
                "floor": "NA",
                "street": "NA",
                "building": "NA",
                "city": "NA",
                "state": "NA",
                "postal_code": "NA",
                # ISO alpha-3, as in every Paymob example (`EGY`).
                "country": "ARE",
            },
            # Diagnostic only. It comes back on the callback unsigned, so it is
            # logged and never used to find an order — see the module docstring.
            "extras": {"order_number": order.order_number},
            # Unique per transaction on Paymob's side; a retry is a new attempt,
            # so it gets a new reference rather than a collision.
            "special_reference": f"{order.order_number}-{uuid.uuid4().hex[:8]}",
            # Matches Stripe's 24h session, and sits inside the 48h
            # `expire_stale_checkouts` sweep — so nobody can pay on a page for an
            # order the sweep has already cancelled.
            "expiration": settings.PAYMOB_CHECKOUT_EXPIRY_SECONDS,
            "notification_url": f"{callback_base}/api/v1/payments/webhooks/paymob",
            "redirection_url": f"{callback_base}/api/v1/payments/paymob/return",
        }

        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/v1/intention/",
                    json=payload,
                    headers=self._secret_headers(),
                )
        except httpx.HTTPError as exc:
            logger.error("Paymob unreachable creating intention: %s", exc)
            raise GatewayUnavailableError(f"Paymob unreachable: {exc}") from exc

        if response.status_code in _UNAVAILABLE_STATUSES | _MISCONFIGURED_STATUSES:
            # 401/403/404 here are ours-but-not-the-order's: a rotated key, a
            # retired integration id. Nothing about this customer's order, so
            # the next gateway may take it rather than card checkout going down.
            logger.error(
                "Paymob returned %s creating intention: %s",
                response.status_code,
                response.text[:500],
            )
            raise GatewayUnavailableError(
                f"Paymob error {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            detail = _error_message(response)
            logger.error(
                "Paymob refused intention for %s: %s %s",
                order.order_number,
                response.status_code,
                detail,
            )
            raise BadRequestError(f"Payment session creation failed: {detail}")

        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayUnavailableError(
                "Paymob returned an intention that was not JSON"
            ) from exc

        paymob_order_id = body.get("intention_order_id")
        client_secret = body.get("client_secret")
        if not paymob_order_id or not client_secret:
            # A 201 with nothing to send the customer to is not a session, and it
            # is Paymob being unwell rather than the order being wrong — so the
            # router may fall back.
            logger.error(
                "Paymob intention for %s had no order id / client secret: %s",
                order.order_number,
                json.dumps(body, default=str)[:500],
            )
            raise GatewayUnavailableError(
                "Paymob returned an intention with no client secret"
            )

        logger.info(
            "Paymob intention created: order=%s paymob_order=%s intention=%s hosted=%s",
            order.order_number,
            paymob_order_id,
            body.get("id"),
            hosted,
        )
        session = GatewaySession(
            session_id=_wrap(_ORDER_PREFIX, paymob_order_id),
            # The Pixel attempt has no page to send anyone to. Leaving it empty
            # is also what keeps `resume_url` from mailing an Apple-Pay-only
            # intention to someone reading the reminder on a laptop.
            checkout_url=self._checkout_url(client_secret) if hosted else "",
            # No payment handle yet: the transaction that will carry one does not
            # exist until the customer pays (see the module docstring).
            payment_id=None,
            raw_status=body.get("status"),
        )
        return session, client_secret

    async def resume_url(self, order: Order) -> str | None:
        """
        The hosted page for a started-but-unpaid order, or None.

        Reads the newest pending *hosted* Paymob attempt's client secret off its
        stored checkout URL and asks Paymob whether that intention can still take
        a payment. Never raises — any doubt reads as "not resumable".
        """
        try:
            attempts = [
                t
                for t in (order.payment_transactions or [])
                if t.gateway == self.code and t.status == "pending" and t.checkout_url
            ]
            if not attempts or not settings.PAYMOB_PUBLIC_KEY:
                return None
            attempt = attempts[-1]
            secret = parse_qs(urlparse(attempt.checkout_url).query).get("clientSecret")
            if not secret:
                return None
            async with self._client() as client:
                response = await client.get(
                    f"{self._base_url()}/v1/intention/element/"
                    f"{settings.PAYMOB_PUBLIC_KEY}/{secret[0]}/"
                )
            if response.status_code >= 400:
                return None
            body = response.json()
            if not isinstance(body, dict):
                return None
            if (
                body.get("confirmed")
                or body.get("status") not in _RESUMABLE_INTENTION_STATUSES
            ):
                return None
            return attempt.checkout_url
        except Exception as exc:  # noqa: BLE001 — "not resumable", by contract
            logger.warning(
                "Paymob resume lookup failed for %s: %s", order.order_number, exc
            )
            return None

    # ── webhooks ──────────────────────────────────────────────────────────────

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        *,
        query: Mapping[str, str] | None = None,
    ) -> GatewayEvent:
        """Verify `?hmac=` over the signed fields and translate `{type, obj}`."""
        try:
            body = json.loads(payload, parse_float=Decimal)
        except ValueError as exc:
            raise BadRequestError("Could not parse webhook payload") from exc
        if not isinstance(body, dict) or not isinstance(body.get("obj"), dict):
            raise BadRequestError("Webhook body was not a Paymob callback")

        presented = (query or {}).get("hmac")
        kind = str(body.get("type") or "TRANSACTION").upper()
        obj: dict = body["obj"]

        if kind == "TOKEN":
            self._verify([obj.get(f) for f in _TOKEN_HMAC_FIELDS], presented)
            return GatewayEvent(
                event_id=f"paymob:token:{obj.get('id')}",
                event_type=PaymentEventType.UNHANDLED,
                raw_type="TOKEN",
            )

        self._verify(
            [_signed_value(obj, f, flat=False) for f in _TRANSACTION_HMAC_FIELDS],
            presented,
        )
        _assert_signed_shape(obj, flat=False)
        data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        migs = (
            data.get("migs_order") if isinstance(data.get("migs_order"), dict) else {}
        )
        return self._event_from_fields(
            obj,
            flat=False,
            acq_code=data.get("acq_response_code"),
            txn_code=data.get("txn_response_code"),
            message=data.get("message"),
            auth_status=migs.get("authenticationStatus"),
            claimed_order=_dig(obj, "payment_key_claims.extra.order_number"),
            unsigned_refunded=obj.get("refunded_amount_cents"),
        )

    def parse_return(self, query: Mapping[str, str]) -> GatewayEvent:
        """
        The customer's browser redirect, as an event.

        The same signed field set, flat on the query string. Used by the return
        bounce to settle an order the moment the customer lands, rather than
        waiting on a callback whose retry behaviour nobody has documented. It is
        exactly as trustworthy as the POST — which is to say, only its signed
        fields are.
        """
        self._verify(
            [_signed_value(query, f, flat=True) for f in _TRANSACTION_HMAC_FIELDS],
            query.get("hmac"),
        )
        _assert_signed_shape(query, flat=True)
        return self._event_from_fields(
            query,
            flat=True,
            acq_code=query.get("acq_response_code"),
            txn_code=query.get("txn_response_code"),
            message=query.get("data.message"),
            auth_status=None,
            claimed_order=query.get("merchant_order_id"),
            unsigned_refunded=query.get("refunded_amount_cents"),
        )

    def _verify(self, values: list[Any], presented: str | None) -> None:
        """
        Constant-time HMAC-SHA512 check.

        Refusing when no secret is configured, rather than waving the request
        through, is the same choice Ziina's provider makes and for the same
        reason: an unsigned endpoint that confirms orders is free cake for anyone
        who can guess an id. The messages all say "signature" so the webhook log
        marks them `signature_valid = false`.
        """
        secret = (settings.PAYMOB_HMAC_SECRET or "").strip()
        if not secret:
            logger.error(
                "Paymob webhook rejected — PAYMOB_HMAC_SECRET is not configured"
            )
            raise BadRequestError("Paymob webhook signature secret not configured")
        if not presented:
            raise BadRequestError("Missing Paymob hmac signature")
        expected = _expected_hmac(values, secret)
        if not hmac.compare_digest(presented.strip().lower(), expected):
            logger.warning("Paymob webhook signature verification failed")
            raise BadRequestError("Invalid webhook signature")

    def _event_from_fields(
        self,
        fields: Mapping[str, Any],
        *,
        flat: bool,
        acq_code: Any,
        txn_code: Any,
        message: Any,
        auth_status: Any,
        claimed_order: Any,
        unsigned_refunded: Any,
    ) -> GatewayEvent:
        """
        Translate a verified transaction into ours — reading decisions only from
        signed fields. `unsigned_refunded` is carried provisionally and replaced
        by `verify_event` before anything acts on it.
        """

        def signed(name: str) -> Any:
            return _signed_value(fields, name, flat=flat)

        txn_id = signed("id")
        paymob_order = signed("order.id")
        amount_cents = _as_int(signed("amount_cents"))
        currency = str(signed("currency") or "")
        integration_id = _as_int(signed("integration_id"))
        success = _truthy(signed("success"))
        pending = _truthy(signed("pending"))
        error_occured = _truthy(signed("error_occured"))

        session_id = _wrap(_ORDER_PREFIX, paymob_order)
        payment_id = _wrap(_TXN_PREFIX, txn_id)
        base = dict(session_id=session_id, order_number=None)

        if claimed_order:
            logger.info(
                "Paymob callback txn=%s paymob_order=%s claims order %s (unsigned; "
                "not used for matching)",
                txn_id,
                paymob_order,
                claimed_order,
            )

        # A transaction from an integration this environment does not own is a
        # test payment arriving at a live endpoint (or the reverse). Test and
        # live integration ids differ, and the id is signed — `is_live` is not.
        if integration_id not in self._integration_ids():
            logger.critical(
                "Paymob callback for integration %s, which is not configured here — "
                "ignored (txn=%s paymob_order=%s)",
                integration_id,
                txn_id,
                paymob_order,
            )
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:foreign-integration",
                event_type=PaymentEventType.UNHANDLED,
                raw_type="foreign_integration",
                **base,
            )

        if _truthy(signed("has_parent_transaction")):
            # The refund or void transaction itself. Not a money fact on its
            # own: `verify_event` resolves it through the parent — read back
            # from Paymob — into the same refund event the parent's own
            # callback produces (same id, so the two dedupe and one refund is
            # never counted twice). `parent_transaction` is unsigned, which is
            # why it is only a pointer to look up, never a figure to believe.
            parent = fields.get("parent_transaction")
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:child",
                event_type=PaymentEventType.UNHANDLED,
                raw_type="child_transaction",
                payment_id=_wrap(_TXN_PREFIX, parent)
                if _DIGITS.match(str(parent or ""))
                else None,
                **base,
            )

        if _truthy(signed("is_voided")):
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:voided",
                event_type=PaymentEventType.REFUNDED,
                raw_type="voided",
                payment_id=payment_id,
                amount_refunded=amount_cents,
                amount_captured=amount_cents,
                fully_refunded=True,
                refund_id=f"paymob:{payment_id}:void",
                cumulative=True,
                **base,
            )

        if _truthy(signed("is_refunded")):
            refunded = _as_int(unsigned_refunded) or 0
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:refunded-{refunded}",
                event_type=PaymentEventType.REFUNDED,
                raw_type="refunded",
                payment_id=payment_id,
                amount_refunded=refunded,
                amount_captured=amount_cents,
                fully_refunded=bool(amount_cents) and refunded >= (amount_cents or 0),
                refund_id=f"paymob:{payment_id}:{refunded}",
                cumulative=True,
                **base,
            )

        if pending:
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:pending",
                event_type=PaymentEventType.UNHANDLED,
                raw_type="pending",
                **base,
            )

        if success:
            if currency.upper() != "AED":
                logger.critical(
                    "Paymob reports a %s payment (txn=%s) — this shop charges AED "
                    "only; not applied",
                    currency,
                    txn_id,
                )
                return GatewayEvent(
                    event_id=f"paymob:txn:{txn_id}:currency",
                    event_type=PaymentEventType.UNHANDLED,
                    raw_type=f"success:{currency}",
                    **base,
                )
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:ok",
                event_type=PaymentEventType.SUCCEEDED,
                raw_type="success",
                payment_id=payment_id,
                amount_captured=amount_cents,
                **base,
            )

        acq = str(acq_code) if acq_code not in (None, "") else None
        txn = str(txn_code) if txn_code not in (None, "") else None
        return GatewayEvent(
            event_id=f"paymob:txn:{txn_id}:fail",
            event_type=PaymentEventType.FAILED,
            raw_type=f"failed:{txn or acq or 'declined'}"[:60],
            # No payment id, deliberately — see the module docstring. The
            # attempt is found by its Paymob order id alone.
            error_code=acq or txn,
            error_message=str(message) if message else None,
            failure_reason=_failure_reason(
                acq, txn, str(auth_status) if auth_status else None, error_occured
            ),
            **base,
        )

    async def verify_event(self, event: GatewayEvent) -> GatewayEvent:
        """
        Confirm a callback against Paymob itself before anything acts on it.

        Two things the signature cannot be trusted for, and one read answers
        both:

        * **Field boundaries.** The HMAC is over the fields concatenated with no
          separator, so a holder of one valid signed set can move digits between
          adjacent numeric fields — `order.id` and `owner` sit side by side —
          and still verify, naming a different Paymob order. The shape checks in
          `_assert_signed_shape` catch most such splits; this catches the rest.
          The transaction is read back by its id and must belong to the same
          Paymob order, with the same outcome and amount. A mismatch is not an
          event we act on.
        * **The refunded amount**, which is outside the signature entirely and
          is *set* onto the order. The figure applied is the one Paymob answers,
          and the event and refund ids are rebuilt from it so dedup and the
          "already booked" check are keyed on the truth.

        Only for events that move money or an order: a pending, foreign,
        child or token callback is applied to nothing and is returned as-is.
        Raises `GatewayUnavailableError` when Paymob cannot be asked, which the
        webhook answers with a 500 (retry) and the reconcile sweep backstops.
        """
        if event.raw_type == "child_transaction" and event.payment_id:
            return await self._refund_from_parent(event)
        if event.event_type not in (
            PaymentEventType.SUCCEEDED,
            PaymentEventType.FAILED,
            PaymentEventType.REFUNDED,
        ):
            return event
        txn_id = event.event_id.split(":")[2]
        body = await self._get_transaction(txn_id)

        fetched_order = _wrap(
            _ORDER_PREFIX, _signed_value(body, "order.id", flat=False)
        )
        fetched_amount = _as_int(body.get("amount_cents"))
        succeeded = _truthy(body.get("success")) and not _truthy(body.get("pending"))
        consistent = fetched_order == event.session_id and (
            event.event_type is not PaymentEventType.SUCCEEDED
            or (succeeded and fetched_amount == event.amount_captured)
        )
        if event.event_type is PaymentEventType.FAILED and succeeded:
            consistent = False
        if not consistent:
            logger.critical(
                "Paymob callback for txn %s does not match Paymob's own record "
                "(callback order %s, Paymob order %s, event %s) — not applied",
                txn_id,
                event.session_id,
                fetched_order,
                event.event_type.value,
            )
            return GatewayEvent(
                event_id=f"paymob:txn:{txn_id}:mismatch",
                event_type=PaymentEventType.UNHANDLED,
                raw_type="mismatch",
                session_id=event.session_id,
            )

        if (
            event.event_type is not PaymentEventType.REFUNDED
            or event.raw_type != "refunded"
        ):
            return event
        refunded = _as_int(body.get("refunded_amount_cents")) or 0
        captured = fetched_amount or event.amount_captured
        return GatewayEvent(
            event_id=f"paymob:txn:{txn_id}:refunded-{refunded}",
            event_type=event.event_type,
            raw_type=event.raw_type,
            order_number=None,
            session_id=event.session_id,
            payment_id=event.payment_id,
            amount_refunded=refunded,
            amount_captured=captured,
            fully_refunded=bool(captured) and refunded >= (captured or 0),
            refund_id=f"paymob:{event.payment_id}:{refunded}",
            cumulative=True,
        )

    async def _refund_from_parent(self, child: GatewayEvent) -> GatewayEvent:
        """
        A refund or void transaction, as the refund of the payment it belongs to.

        Paymob may report a refund on the child transaction rather than (or as
        well as) on the parent. The parent is read from Paymob and must belong
        to the same signed Paymob order; its own cumulative figure is what is
        applied, under the id the parent's callback would carry.
        """
        parent_id = _unwrap(_TXN_PREFIX, child.payment_id)
        body = await self._get_transaction(parent_id)
        parent_order = _wrap(_ORDER_PREFIX, _signed_value(body, "order.id", flat=False))
        if parent_order != child.session_id:
            logger.critical(
                "Paymob child transaction points at %s, which is not on Paymob "
                "order %s — not applied",
                parent_id,
                child.session_id,
            )
            return GatewayEvent(
                event_id=child.event_id,
                event_type=PaymentEventType.UNHANDLED,
                raw_type="child_mismatch",
                session_id=child.session_id,
            )
        captured = _as_int(body.get("amount_cents"))
        payment_id = _wrap(_TXN_PREFIX, parent_id)
        if _truthy(body.get("is_voided")):
            return GatewayEvent(
                event_id=f"paymob:txn:{parent_id}:voided",
                event_type=PaymentEventType.REFUNDED,
                raw_type="voided",
                session_id=child.session_id,
                payment_id=payment_id,
                amount_refunded=captured,
                amount_captured=captured,
                fully_refunded=True,
                refund_id=f"paymob:{payment_id}:void",
                cumulative=True,
            )
        refunded = _as_int(body.get("refunded_amount_cents")) or 0
        if refunded <= 0:
            return child
        return GatewayEvent(
            event_id=f"paymob:txn:{parent_id}:refunded-{refunded}",
            event_type=PaymentEventType.REFUNDED,
            raw_type="refunded",
            session_id=child.session_id,
            payment_id=payment_id,
            amount_refunded=refunded,
            amount_captured=captured,
            fully_refunded=bool(captured) and refunded >= (captured or 0),
            refund_id=f"paymob:{payment_id}:{refunded}",
            cumulative=True,
        )

    async def fetch_outcome(self, attempt) -> GatewayEvent | None:
        """
        What Paymob says happened to *attempt*, for the reconcile sweep.

        A transaction inquiry by the Paymob order id returns that order's most
        recent transaction. It is read with our own credentials from Paymob's own
        API, so unlike a callback there is no signature to check — but it goes
        through the same translation, so a success here and a success by
        webhook are the same event with the same id and dedupe against each
        other. Returns None when there is nothing yet (no transaction).
        """
        if attempt.gateway != self.code or not attempt.session_id:
            return None
        paymob_order = _unwrap(_ORDER_PREFIX, attempt.session_id)
        token = await self._bearer_token()
        try:
            body = await self._read(
                "POST",
                "/api/ecommerce/orders/transaction_inquiry",
                json={"auth_token": token, "order_id": paymob_order},
            )
        except BadRequestError:
            # Paymob refusing the question — typically "not found" for an order
            # nobody ever paid on — is an answer: there is no payment. Only an
            # outage (`GatewayUnavailableError`) is "maybe", and it propagates.
            return None
        if not body.get("id"):
            return None
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        migs = (
            data.get("migs_order") if isinstance(data.get("migs_order"), dict) else {}
        )
        event = self._event_from_fields(
            body,
            flat=False,
            acq_code=data.get("acq_response_code"),
            txn_code=data.get("txn_response_code"),
            message=data.get("message"),
            auth_status=migs.get("authenticationStatus"),
            claimed_order=None,
            unsigned_refunded=body.get("refunded_amount_cents"),
        )
        # An inquiry about *this* attempt must answer about this attempt's
        # Paymob order; anything else is not evidence about it.
        if event.session_id != attempt.session_id:
            return None
        return event

    # ── refunds ───────────────────────────────────────────────────────────────

    async def refund(
        self,
        *,
        payment_id: str,
        amount: Decimal,
        idempotency_key: str,
        test_mode: bool = False,
        expected_prior_refunded: Decimal | None = None,
    ) -> GatewayRefund:
        """
        Refund part or all of a Paymob transaction.

        **Idempotency, supplied from our side.** Paymob takes no key, so the
        caller's `idempotency_key` has nothing to bind to. What stands in for it
        is a read before the write: the transaction's `refunded_amount_cents` is
        compared with `expected_prior_refunded` — what our books say had been
        refunded before this request, a figure the caller reads from columns a
        rollback restores.

        * No difference: nothing is unaccounted for, so the refund is sent.
        * A difference of **exactly** this request's amount: this is our own
          earlier attempt, whose money moved and whose transaction rolled back.
          It is reported as done and not sent again — which is the double
          refund this whole method exists to prevent. (A refund of the very
          same amount made by hand on Paymob's dashboard looks identical, and
          is booked the same way. That is the safe reading of the ambiguity:
          the customer has that money back either way, and sending again would
          be the overpayment.)
        * Any other difference: someone refunded on Paymob outside this
          application and its callback has not landed. Booking it as this
          request's result would skip the refund a person just asked for, and
          sending ours on top might overpay; so it refuses and says why.

        The refund id is built from Paymob's cumulative figure — the same string
        the parent transaction's refund callback produces (`verify_event`) — so
        `_refund_already_recorded` recognises that callback as an acknowledgement.
        """
        if not self.is_configured():
            raise BadRequestError("Paymob is not configured in this environment")

        txn_id = _unwrap(_TXN_PREFIX, payment_id)
        requested = int(money(amount) * 100)
        if requested <= 0:
            raise BadRequestError("Refund amount must be greater than zero")

        current = await self._get_transaction(txn_id)
        remote = _as_int(current.get("refunded_amount_cents")) or 0
        prior = int(money(expected_prior_refunded or 0) * 100)
        unaccounted = remote - prior

        if unaccounted == requested:
            logger.warning(
                "Paymob already shows %s fils refunded on %s beyond our books — "
                "treating it as this refund's earlier attempt, not re-sending",
                unaccounted,
                payment_id,
            )
            return GatewayRefund(
                refund_id=f"paymob:{payment_id}:{remote}",
                amount=money(amount),
                status="completed",
                raw_status="already_refunded",
            )
        if unaccounted != 0:
            raise BadRequestError(
                f"Paymob shows AED {Decimal(remote) / 100:.2f} refunded on this "
                f"payment, but only AED {Decimal(prior) / 100:.2f} is recorded here. "
                "Reconcile it before refunding again."
            )

        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/api/acceptance/void_refund/refund",
                    json={"transaction_id": int(txn_id), "amount_cents": requested},
                    headers=self._secret_headers(),
                )
        except httpx.HTTPError as exc:
            # The outcome is unknown. The next attempt's read-back is what tells
            # a refund that landed from one that did not.
            logger.error("Paymob unreachable issuing refund: %s", exc)
            raise GatewayUnavailableError(f"Paymob unreachable: {exc}") from exc

        if response.status_code in _UNAVAILABLE_STATUSES:
            raise GatewayUnavailableError(
                f"Paymob error {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            detail = _error_message(response)
            logger.error("Paymob refused a refund for %s: %s", payment_id, detail)
            raise BadRequestError(f"Refund failed: {detail}")

        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayUnavailableError(
                "Paymob returned a refund that was not JSON"
            ) from exc

        if not _truthy(body.get("success")) and not _truthy(body.get("pending")):
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            raise BadRequestError(
                f"Paymob declined the refund: {data.get('message') or 'no reason given'}"
            )

        # What was actually sent back, from Paymob's reply rather than our
        # request — a processor may refund less than asked.
        # Capped at what was asked: the reply's `amount_cents` is the refund
        # transaction's own amount in the one example there is, and if Paymob
        # ever answers with the parent instead, believing it would book the
        # whole charge as refunded after a partial.
        refunded = min(_as_int(body.get("amount_cents")) or requested, requested)
        pending = _truthy(body.get("pending"))
        return GatewayRefund(
            refund_id=f"paymob:{payment_id}:{remote + refunded}",
            amount=money(Decimal(refunded) / 100),
            status="pending" if pending else "completed",
            raw_status="pending" if pending else "success",
        )


# Module-level singleton — imported by the gateway registry
provider = PaymobProvider()
