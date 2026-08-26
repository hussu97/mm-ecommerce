from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Mapping

import stripe
from stripe._error import (
    APIConnectionError,
    IdempotencyError,
    SignatureVerificationError,
    StripeError,
)

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models.order import Order
from app.services.providers import checkout_urls
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

_PAYMENT_INTENT_PREFIX = "pi_"

#: Stripe refuses an AED charge under 2.00 at its own edge.
_AED_MINIMUM = Decimal("2.00")

#: Stripe's own status codes that mean "us, not you". A 5xx or a dropped
#: connection is a reason to try the other processor; a 402 declined card is
#: emphatically not, and neither is a 400 about a malformed line item.
_UNAVAILABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def _fee_label(delivery_fee: Decimal, low_order_fee: Decimal) -> str:
    """
    What to call the combined fee line, given what is actually in it.

    The line is one line for the reasons given at the call site, but it must not
    lie about what it is. Sharjah delivers free and still charges the
    small-basket fee, so a fixed "Delivery Fee" would put `Delivery Fee AED
    15.00` on the payment page of an order whose delivery is free — the one
    place a customer is looking hardest at what they are about to be charged,
    and a discrepancy they will read as an error and abandon over.
    """
    if delivery_fee > 0 and low_order_fee > 0:
        return "Delivery & small order fee"
    if low_order_fee > 0:
        return "Small order fee"
    return "Delivery Fee"


def coupon_id(order: Order) -> str:
    """
    The one coupon this order's discount is ever expressed as.

    Named off the order rather than minted fresh, and that is the whole point.
    A `Coupon.create` with no id returns a new random one every call, so the
    `discounts` argument to `Session.create` was different on every attempt at
    the same order — while the idempotency key stayed `sess_{order_number}`.
    Stripe's rule is that one key must always carry one payload, so the *second*
    time a customer opened the payment page for a discounted order it answered
    400 `idempotency_error` and no page opened at all.

    That is not a rare corner: it is every customer who reaches Stripe, changes
    their mind, and comes back. MM-20260820-001 is the worked example — two
    refusals ten seconds apart, then a whole new order placed from scratch, with
    the abandoned one still holding a use of the coupon it was discounted with.

    An order's discount is fixed when the order is written, so one coupon per
    order is not a cache, it is the truth: the same id always describes the same
    money off.
    """
    return f"order-{order.order_number}"


def _discounts_for(order: Order) -> list[dict]:
    """
    The `discounts` argument, stable across every attempt at this order.

    Returns `[]` for an undiscounted order and — deliberately — for one whose
    coupon could not be created. A payment page charging full price is wrong,
    but it is recoverable by a person; no payment page at all is a customer who
    leaves. The warning is what brings somebody to it.
    """
    if not order.discount_amount or order.discount_amount <= 0:
        return []

    identifier = coupon_id(order)
    try:
        stripe.Coupon.create(
            id=identifier,
            amount_off=int(order.discount_amount * 100),
            currency="aed",
            duration="once",
            name=f"Promo: {order.promo_code_used or 'discount'}",
        )
    except StripeError as exc:
        # The ordinary path on every attempt after the first: the coupon this
        # order needs already exists, which is exactly what asking for it by
        # name is for. Anything else is a real failure and is logged as one.
        if getattr(exc, "code", None) != "resource_already_exists":
            logger.warning("Could not create Stripe coupon: %s", exc)
            return []
    return [{"coupon": identifier}]


class StripeProvider(PaymentGatewayProvider):
    """Stripe Checkout Sessions + webhook handler."""

    code = "stripe"

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _configure() -> None:
        stripe.api_key = settings.STRIPE_SECRET_KEY

    # ── PaymentGatewayProvider interface ──────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(settings.STRIPE_SECRET_KEY)

    def minimum_amount(self) -> Decimal | None:
        return _AED_MINIMUM

    async def create_session(
        self, order: Order, *, test_mode: bool = False
    ) -> GatewaySession:
        """
        Create a Stripe Checkout Session for the given order.

        `test_mode` is ignored: Stripe decides live-versus-test from the secret
        key itself, so honouring a per-session flag here would let the admin
        appear to switch an environment it has no way of switching.

        Async in signature only. `stripe-python`'s sync client blocks the event
        loop for the length of the call, which it has always done here — this
        change neither improves that nor makes it worse. Moving Stripe onto the
        SDK's `*_async` methods is a change against the one gateway currently
        taking live money, and does not belong in the same commit as the gateway
        it is being made interchangeable with.
        """
        self._configure()

        line_items = [
            {
                "price_data": {
                    "currency": "aed",
                    "unit_amount": int(item.unit_price * 100),
                    "product_data": {
                        "name": item.product_name,
                        "description": item.product_sku or item.product_name,
                    },
                },
                "quantity": item.quantity,
            }
            for item in order.items
        ]

        # Every fee on the order, as one line.
        #
        # They are separate columns and separate lines on the checkout and the
        # confirmation email, because a customer asking "why is there an extra
        # fifteen dirhams" deserves an answer. Stripe is not that surface — it is
        # a payment page the customer passes through, and splitting the charge
        # there buys nothing while adding a second place the total can drift
        # from `order.total`.
        #
        # Summed rather than listed, and the sum is what must equal the order:
        # a fee that exists on the order and not here charges the card less than
        # the order says, and nothing notices, because both totals are
        # internally consistent. `test_stripe_line_items_add_up_to_the_order_total`
        # is what holds this to it.
        delivery_fee = order.delivery_fee or Decimal("0")
        low_order_fee = order.low_order_fee or Decimal("0")
        fees = delivery_fee + low_order_fee
        if fees > 0:
            line_items.append(
                {
                    "price_data": {
                        "currency": "aed",
                        "unit_amount": int(fees * 100),
                        "product_data": {
                            "name": _fee_label(delivery_fee, low_order_fee)
                        },
                    },
                    "quantity": 1,
                }
            )

        discounts = _discounts_for(order)

        # The email rides along so the confirmation page can prove ownership to
        # GET /orders/{order_number} even if the guest session cookie was lost.
        success_url = checkout_urls.success_url(
            order, reference_token="{CHECKOUT_SESSION_ID}"
        )
        cancel_url = checkout_urls.cancel_url(order)

        def create(idempotency_key: str):
            return stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=line_items,
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=order.email,
                metadata={
                    "order_number": order.order_number,
                    "order_id": str(order.id),
                },
                discounts=discounts,
                payment_intent_data={
                    "metadata": {
                        "order_number": order.order_number,
                        "order_id": str(order.id),
                    },
                },
                idempotency_key=idempotency_key,
            )

        try:
            try:
                session = create(f"sess_{order.order_number}")
            except IdempotencyError as exc:
                # The key is the order, so Stripe replays the first session for
                # every later attempt on it — which is the behaviour we want:
                # one order, one payment page, whichever tab the customer is on.
                # It refuses outright when the *parameters* differ, and every
                # difference this has ever had was a bug rather than a genuinely
                # different request.
                #
                # The one that reached production minted a fresh Stripe coupon
                # on every call, so a discounted order's `discounts` never
                # matched twice. MM-20260820-001 was that: session created at
                # 05:16, customer went back, two 400s at 05:18, no payment page
                # either time, and a second order placed from scratch at 05:28.
                # The stable coupon id in `_discounts_for` is the fix.
                #
                # This stays underneath it because a dead checkout button is the
                # worst outcome available here, and any future drift in this
                # payload would produce exactly that. A second Checkout Session
                # is a cheap thing to be wrong about — only one can be paid, both
                # carry the order number, and the loser expires into
                # `checkout.session.expired`, which is ignored for an order that
                # is no longer `created`.
                logger.warning(
                    "Stripe refused the idempotent session for %s (%s); "
                    "opening a fresh one so the customer is not stranded",
                    order.order_number,
                    exc,
                )
                session = create(f"sess_{order.order_number}_{uuid.uuid4().hex[:12]}")
        except APIConnectionError as e:
            # Never reached Stripe at all. The order is fine; the road is not.
            logger.error("Stripe unreachable creating session: %s", e)
            raise GatewayUnavailableError(f"Stripe unreachable: {e}") from e
        except StripeError as e:
            status = getattr(e, "http_status", None)
            if status in _UNAVAILABLE_STATUSES:
                logger.error("Stripe returned %s creating session: %s", status, e)
                raise GatewayUnavailableError(f"Stripe error {status}: {e}") from e
            logger.error("Stripe session creation failed: %s", e)
            raise BadRequestError(
                f"Payment session creation failed: {getattr(e, 'user_message', None) or str(e)}"
            )

        return GatewaySession(
            session_id=session.id,
            checkout_url=session.url,
            # Stripe's Payment Intent does not exist until the customer pays, so
            # there is deliberately nothing to record here yet. The confirmation
            # webhook is what fills it in.
            payment_id=None,
            raw_status=getattr(session, "status", None),
        )

    async def create_payment_intent(self, order: Order, *, idempotency_key: str):
        """
        A PaymentIntent for an in-page wallet payment (Apple Pay via Stripe.js).

        Unlike `create_session`, which hands the customer to Stripe's hosted
        page, this returns a `client_secret` the browser confirms itself — the
        Payment Request / Apple Pay path. The money still lands the ordinary
        way: the intent carries `order_number` in its metadata, so
        `payment_intent.succeeded` reconciles it through the exact webhook path
        every hosted-Checkout card payment already uses. Nothing downstream is
        Apple-Pay-aware.

        `payment_method_types=["card"]` on purpose: an Apple Pay token *is* a
        card payment method to Stripe, so this is the same processor surface as
        the hosted session and cannot pull in a redirect-based method the
        in-page confirmation could not complete.

        The idempotency key is the order, so a customer who dismisses the sheet
        and taps again gets the *same* intent rather than a second one — one
        order, one charge.
        """
        self._configure()

        amount_minor = int(Decimal(str(order.total)) * 100)

        try:
            intent = stripe.PaymentIntent.create(
                amount=amount_minor,
                currency="aed",
                payment_method_types=["card"],
                description=f"Order {order.order_number}",
                receipt_email=order.email or None,
                metadata={
                    "order_number": order.order_number,
                    "order_id": str(order.id),
                },
                idempotency_key=idempotency_key,
            )
        except APIConnectionError as e:
            logger.error("Stripe unreachable creating payment intent: %s", e)
            raise GatewayUnavailableError(f"Stripe unreachable: {e}") from e
        except StripeError as e:
            status = getattr(e, "http_status", None)
            if status in _UNAVAILABLE_STATUSES:
                logger.error(
                    "Stripe returned %s creating payment intent: %s", status, e
                )
                raise GatewayUnavailableError(f"Stripe error {status}: {e}") from e
            logger.error("Stripe payment intent creation failed: %s", e)
            raise BadRequestError(
                "Payment could not be started: "
                f"{getattr(e, 'user_message', None) or str(e)}"
            )

        return intent

    async def refund(
        self,
        *,
        payment_id: str,
        amount: Decimal,
        idempotency_key: str,
        test_mode: bool = False,
    ) -> GatewayRefund:
        """
        Refund part or all of a Payment Intent.

        `test_mode` is ignored for the same reason `create_session` ignores it:
        the secret key decides live-versus-test, and a per-call flag would only
        appear to switch an environment it cannot switch.

        The idempotency key is Stripe's own header, so a second call with the
        same key returns the *first* refund rather than making another. That
        matters more here than anywhere else in this file — the caller is a
        status transition reachable from an admin click, a webhook and a retry
        sweep, and without this the third of those pays the customer twice.

        `pending` is a success. Card refunds settle over days, and treating
        anything but `succeeded` as failure would have the shop refund again.
        """
        self._configure()
        try:
            refund = stripe.Refund.create(
                payment_intent=payment_id,
                amount=int(amount * 100),
                idempotency_key=idempotency_key,
            )
        except APIConnectionError as exc:
            # The processor, not the request. The caller may try again — and
            # will, with the same key, so a refund that actually went through
            # before the connection dropped is not duplicated.
            raise GatewayUnavailableError(f"Stripe was unreachable: {exc}") from exc
        except StripeError as exc:
            # Everything else is the request: already refunded, more than was
            # captured, a payment intent that never succeeded. Retrying does not
            # fix any of them.
            raise BadRequestError(f"Stripe refused the refund: {exc}") from exc

        raw_status = str(refund.get("status") or "")
        return GatewayRefund(
            refund_id=str(refund.get("id") or ""),
            # Stripe answers in minor units, and answers with what it actually
            # did rather than what was asked for.
            amount=Decimal(refund.get("amount") or 0) / 100,
            status=_REFUND_STATUSES.get(raw_status, "pending"),
            raw_status=raw_status,
        )

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> GatewayEvent:
        """
        Verify a Stripe webhook and translate it into a `GatewayEvent`.

        Handles, and maps:
          - payment_intent.succeeded      → SUCCEEDED
          - payment_intent.payment_failed → FAILED
          - payment_intent.canceled       → CANCELLED
          - checkout.session.expired      → EXPIRED
          - charge.refunded               → REFUNDED
          - charge.dispute.created        → DISPUTED
        Everything else → UNHANDLED, which is acknowledged and applied to nothing.
        """
        signature = _header(headers, "stripe-signature")
        if not signature:
            raise BadRequestError("Missing Stripe-Signature header")

        if not settings.STRIPE_WEBHOOK_SECRET:
            raise BadRequestError("Stripe webhook secret not configured")

        try:
            stripe.Webhook.construct_event(
                payload, signature, settings.STRIPE_WEBHOOK_SECRET
            )
        except SignatureVerificationError as e:
            logger.warning("Stripe webhook signature verification failed: %s", e)
            raise BadRequestError("Invalid webhook signature")
        except Exception as e:
            logger.error("Stripe webhook parsing error: %s", e)
            raise BadRequestError("Could not parse webhook payload")

        # Verified above; read below. The fields are taken out of the raw JSON
        # rather than off the SDK's object model, deliberately.
        #
        # `construct_event` returns typed resources — `event["data"]["object"]`
        # is a `PaymentIntent`, not a dict — and since stripe-python 8 those no
        # longer subclass dict, so `.get()` raises `AttributeError: get`. That is
        # exactly what happened: every `payment_intent.succeeded` since the SDK
        # was upgraded threw there, and orders that were genuinely paid sat in
        # `created`. The SDK's job is to prove the payload is authentic; JSON we
        # already trust is a shape that cannot be changed out from under us by a
        # minor version bump.
        try:
            body = json.loads(payload)
        except ValueError:
            raise BadRequestError("Could not parse webhook payload")

        raw_type: str = body.get("type", "")
        obj: dict = (body.get("data") or {}).get("object") or {}
        metadata: dict = obj.get("metadata") or {}

        order_number: str | None = None
        payment_id: str | None = None
        session_id: str | None = None
        amount_refunded: int | None = None
        amount_captured: int | None = None
        fully_refunded: bool | None = None
        error_code: str | None = None
        error_message: str | None = None
        failure_reason: PaymentFailureReason | None = None

        if raw_type.startswith("payment_intent."):
            payment_id = obj.get("id")
            order_number = metadata.get("order_number")
            last_error = obj.get("last_payment_error") or {}
            # `decline_code` is the reason the *bank* gave and the only field
            # granular enough to tell a customer anything useful — `code` on a
            # declined card is almost always the bare "card_declined". We keep
            # the granular one as `error_code` (falling back to `code` for the
            # input errors that carry no decline_code, e.g. `incorrect_cvc`) so
            # reconciliation sees the specific reason, and normalise the pair
            # into the bucket the storefront actually shows.
            decline_code = last_error.get("decline_code")
            code = last_error.get("code")
            error_code = decline_code or code
            error_message = last_error.get("message")
            failure_reason = _failure_reason(code, decline_code)
        elif raw_type == "charge.dispute.created":
            payment_id = obj.get("payment_intent")
        elif raw_type.startswith("charge."):
            payment_id = obj.get("payment_intent")
            order_number = metadata.get("order_number")
            # A refund is not automatically the whole order. `refunded` is
            # Stripe's own "nothing left on this charge" flag; the amounts are
            # carried too so a partial can be reported accurately in minor units.
            amount_refunded = obj.get("amount_refunded")
            amount_captured = obj.get("amount")
            fully_refunded = obj.get("refunded")
        elif raw_type.startswith("checkout.session."):
            session_id = obj.get("id")
            payment_id = obj.get("payment_intent")
            order_number = metadata.get("order_number")

        logger.info(
            "Stripe webhook: type=%s order=%s payment_intent=%s",
            raw_type,
            order_number,
            payment_id,
        )

        return GatewayEvent(
            event_id=body.get("id"),
            event_type=_EVENT_TYPES.get(raw_type, PaymentEventType.UNHANDLED),
            raw_type=raw_type,
            order_number=order_number,
            session_id=session_id,
            payment_id=payment_id,
            amount_refunded=amount_refunded,
            amount_captured=amount_captured,
            fully_refunded=fully_refunded,
            error_code=error_code,
            error_message=error_message,
            failure_reason=failure_reason,
        )

    def is_confirmed_payment_id(self, payment_id: str | None) -> bool:
        """
        True when *payment_id* is a confirmed Payment Intent (not a session).

        Kept only for orders written before `payment_transactions` existed,
        which have no row to read and whose paid-ness is recoverable from
        nothing but this prefix. New code asks the transaction; see
        `payment_service._is_paid`.
        """
        return bool(payment_id and payment_id.startswith(_PAYMENT_INTENT_PREFIX))


#: Stripe's decline vocabulary → the buckets the customer is shown.
#:
#: Keyed by both `decline_code` (the bank's reason) and `code` (the API-level
#: error), because the same table has to answer for either: a declined card
#: sends `code="card_declined"` + a `decline_code`, while an input error like a
#: bad CVC sends only `code="incorrect_cvc"`. Whichever we have, we look it up.
#:
#: The list is Stripe's own (https://docs.stripe.com/declines/codes), grouped by
#: the shopper's next move. The sensitive reasons — `fraudulent`, `lost_card`,
#: `stolen_card`, `merchant_blacklist`, `pickup_card` — are folded into
#: `CARD_DECLINED` on purpose: Stripe's rule is that they must be presented as an
#: ordinary decline, never named to the customer.
_FAILURE_REASONS: dict[str, PaymentFailureReason] = {
    # Not enough money / over a limit.
    "insufficient_funds": PaymentFailureReason.INSUFFICIENT_FUNDS,
    "card_velocity_exceeded": PaymentFailureReason.INSUFFICIENT_FUNDS,
    "withdrawal_count_limit_exceeded": PaymentFailureReason.INSUFFICIENT_FUNDS,
    # Expired, or an expiry that cannot be right.
    "expired_card": PaymentFailureReason.EXPIRED_CARD,
    "invalid_expiry_month": PaymentFailureReason.EXPIRED_CARD,
    "invalid_expiry_year": PaymentFailureReason.EXPIRED_CARD,
    # Wrong security code.
    "incorrect_cvc": PaymentFailureReason.INCORRECT_CVC,
    "invalid_cvc": PaymentFailureReason.INCORRECT_CVC,
    # Wrong card number.
    "incorrect_number": PaymentFailureReason.INCORRECT_NUMBER,
    "invalid_number": PaymentFailureReason.INCORRECT_NUMBER,
    # Wrong billing postcode / address.
    "incorrect_zip": PaymentFailureReason.INCORRECT_DETAILS,
    "incorrect_address": PaymentFailureReason.INCORRECT_DETAILS,
    # Card / currency / transaction type not usable here.
    "card_not_supported": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "currency_not_supported": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "not_permitted": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "transaction_not_allowed": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "service_not_allowed": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "invalid_account": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "invalid_amount": PaymentFailureReason.CARD_NOT_SUPPORTED,
    "new_account_information_available": PaymentFailureReason.CARD_NOT_SUPPORTED,
    # Bank wants 3-D Secure / step-up.
    "authentication_required": PaymentFailureReason.AUTHENTICATION_REQUIRED,
    "authentication_not_handled": PaymentFailureReason.AUTHENTICATION_REQUIRED,
    "mobile_device_authentication_required": (
        PaymentFailureReason.AUTHENTICATION_REQUIRED
    ),
    # Transient — "try again" is the honest advice.
    "processing_error": PaymentFailureReason.PROCESSING_ERROR,
    "reenter_transaction": PaymentFailureReason.PROCESSING_ERROR,
    "issuer_not_available": PaymentFailureReason.PROCESSING_ERROR,
    "approve_with_id": PaymentFailureReason.PROCESSING_ERROR,
    "try_again_later": PaymentFailureReason.PROCESSING_ERROR,
    # A near-identical charge just went through.
    "duplicate_transaction": PaymentFailureReason.DUPLICATE,
    # Unknowable bank refusals, and the reasons we must not name — all shown as
    # a plain decline.
    "do_not_honor": PaymentFailureReason.CARD_DECLINED,
    "generic_decline": PaymentFailureReason.CARD_DECLINED,
    "call_issuer": PaymentFailureReason.CARD_DECLINED,
    "no_action_taken": PaymentFailureReason.CARD_DECLINED,
    "revocation_of_all_authorizations": PaymentFailureReason.CARD_DECLINED,
    "revocation_of_authorization": PaymentFailureReason.CARD_DECLINED,
    "security_violation": PaymentFailureReason.CARD_DECLINED,
    "stop_payment_order": PaymentFailureReason.CARD_DECLINED,
    "fraudulent": PaymentFailureReason.CARD_DECLINED,
    "lost_card": PaymentFailureReason.CARD_DECLINED,
    "stolen_card": PaymentFailureReason.CARD_DECLINED,
    "merchant_blacklist": PaymentFailureReason.CARD_DECLINED,
    "pickup_card": PaymentFailureReason.CARD_DECLINED,
    "restricted_card": PaymentFailureReason.CARD_DECLINED,
    "card_declined": PaymentFailureReason.CARD_DECLINED,
}


def _failure_reason(code: str | None, decline_code: str | None) -> PaymentFailureReason:
    """
    The customer-shown bucket for a Stripe failure.

    `decline_code` first — it is the specific reason — then `code`, then the
    safe default. There is *always* a bucket: a code Stripe adds tomorrow that
    we have not mapped is a decline the customer should still be told is a
    decline, and `CARD_DECLINED`'s copy ("contact your bank or try another
    card") is true of every one of them.
    """
    return (
        _FAILURE_REASONS.get(decline_code or "")
        or _FAILURE_REASONS.get(code or "")
        or PaymentFailureReason.CARD_DECLINED
    )


#: Stripe's vocabulary → ours. A dict rather than a chain of `if`s because it is
#: a translation table, and the thing a reader wants from it is the whole
#: mapping at once.
_EVENT_TYPES: dict[str, PaymentEventType] = {
    "payment_intent.succeeded": PaymentEventType.SUCCEEDED,
    "payment_intent.payment_failed": PaymentEventType.FAILED,
    "payment_intent.canceled": PaymentEventType.CANCELLED,
    # Stripe gives a Checkout Session 24 hours and then says this, once, with
    # our `order_number` in its metadata. Until it was mapped, an abandoned
    # checkout sat at `created` forever: MM-20260820-001 was a customer who
    # opened the payment page at 05:16, went back, and re-ordered at 05:28 —
    # and the first order kept a redemption of the NEW code and one of that
    # customer's three "first orders" for a sale that never happened.
    "checkout.session.expired": PaymentEventType.EXPIRED,
    "charge.refunded": PaymentEventType.REFUNDED,
    "charge.dispute.created": PaymentEventType.DISPUTED,
}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """
    Case-insensitively read a header out of whatever mapping we were handed.

    Starlette's own `request.headers` is already case-insensitive; a plain dict
    built in a test is not, and a signature check that passes in production and
    fails in a test for that reason is a bad afternoon.
    """
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


#: Stripe's refund statuses onto the three this application uses, which are
#: Ziina's — chosen because they are the smaller, plainer set and because a
#: refund only ever needs to answer "did it work, is it still going, or did it
#: fail". `requires_action` is rare (a few bank rails) and is a pending refund
#: from the shop's point of view: nobody here can take the action.
_REFUND_STATUSES = {
    "succeeded": "completed",
    "pending": "pending",
    "requires_action": "pending",
    "failed": "failed",
    "canceled": "failed",
}


# Module-level singleton — imported by the gateway registry
provider = StripeProvider()
