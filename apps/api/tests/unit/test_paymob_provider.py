"""
Paymob — the gateway that signs twenty fields and trusts the rest to nobody.

What these pin, in the order a payment meets them:

* The signature is Paymob's exact concatenation (the docs' own worked example
  is the golden vector), over the POST body and over the redirect's flat query,
  and anything else — forged, missing, no secret configured — is refused.
* Only signed fields decide anything: the order is found by the signed Paymob
  order id and never by our (unsigned) `extras`; a transaction from an
  integration this environment does not own is ignored; a declined transaction
  carries no payment handle, so a later refund cannot aim at it.
* Handles are namespaced (`ord_`/`txn_`), so a transaction id can never be
  mistaken for another order's Paymob order id.
* Refunds, which Paymob takes with no idempotency key, are made retry-safe by a
  read-back: an earlier attempt that landed is recognised, an unexplained one is
  refused, and the refund id matches the one the refund callback will carry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.services.providers import paymob_provider as pm
from app.services.providers.base import (
    GatewayUnavailableError,
    PaymentEventType,
    PaymentFailureReason,
)

HMAC_SECRET = "paymob_hmac_secret"
CARD_ID = 4097558
APPLE_ID = 4097559

#: The string Paymob's docs give for their worked example, byte for byte.
#: https://developers.paymob.com/paymob-docs/developers/webhook-callbacks-and-hmac/hmac/hmac-transaction-callback
DOC_CONCATENATION = (
    "1000002024-06-13T11:33:44.592345EGPfalsefalse1920364654097558truefalsefalse"
    "falsetruefalse217503754302852false2346MasterCardcardtrue"
)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for name, value in {
        "PAYMOB_ENABLED": True,
        "PAYMOB_SECRET_KEY": "sk_test_x",
        "PAYMOB_PUBLIC_KEY": "pk_test_x",
        "PAYMOB_API_KEY": "api_key_x",
        "PAYMOB_HMAC_SECRET": HMAC_SECRET,
        "PAYMOB_CARD_INTEGRATION_ID": CARD_ID,
        "PAYMOB_APPLE_PAY_INTEGRATION_ID": APPLE_ID,
        "PAYMOB_CALLBACK_BASE_URL": "https://api.example.test",
        "PAYMOB_API_URL": "https://uae.paymob.test",
        "PAYMOB_CHECKOUT_URL": "https://uae.paymob.test/unifiedcheckout",
    }.items():
        monkeypatch.setattr(settings, name, value)
    pm._bearer.update(token=None, expires=0.0)


def _obj(**over) -> dict:
    """A maximal processed-callback `obj`, successful and AED unless told otherwise."""
    obj = {
        "id": 192036465,
        "pending": False,
        "amount_cents": 12500,
        "success": True,
        "is_auth": False,
        "is_capture": False,
        "is_standalone_payment": True,
        "is_voided": False,
        "is_refunded": False,
        "is_3d_secure": True,
        "integration_id": CARD_ID,
        "has_parent_transaction": False,
        "order": {"id": 217503754, "merchant_order_id": "MM-20260925-001-abcd"},
        "created_at": "2026-09-25T11:33:44.592345",
        "currency": "AED",
        "source_data": {"pan": "2346", "type": "card", "sub_type": "MasterCard"},
        "error_occured": False,
        "owner": 302852,
        "refunded_amount_cents": 0,
        "data": {
            "message": "Approved",
            "txn_response_code": "APPROVED",
            "acq_response_code": "00",
        },
        "payment_key_claims": {"extra": {"order_number": "MM-20260925-001"}},
    }
    for key, value in over.items():
        obj[key] = value
    return obj


def _sign(obj: dict, secret: str = HMAC_SECRET) -> str:
    values = [pm._signed_value(obj, f, flat=False) for f in pm._TRANSACTION_HMAC_FIELDS]
    return pm._expected_hmac(values, secret)


def _post(obj: dict, *, hmac_value: str | None = None, kind: str = "TRANSACTION"):
    body = json.dumps({"type": kind, "obj": obj}).encode()
    return body, {"hmac": hmac_value if hmac_value is not None else _sign(obj)}


def _flat(obj: dict) -> dict[str, str]:
    """The redirect's query string for the same transaction."""
    flat: dict[str, str] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            continue
        flat[key] = (
            "true" if value is True else "false" if value is False else str(value)
        )
    flat["order"] = str(obj["order"]["id"])
    for k, v in obj["source_data"].items():
        flat[f"source_data.{k}"] = str(v)
    flat["data.message"] = obj["data"]["message"]
    flat["txn_response_code"] = obj["data"]["txn_response_code"]
    flat["acq_response_code"] = obj["data"]["acq_response_code"]
    values = [pm._signed_value(flat, f, flat=True) for f in pm._TRANSACTION_HMAC_FIELDS]
    flat["hmac"] = pm._expected_hmac(values, HMAC_SECRET)
    return flat


def _transport(monkeypatch, handler):
    """Route every Paymob call through *handler* (an httpx MockTransport)."""
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        pm.PaymobProvider,
        "_client",
        staticmethod(
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording))
        ),
    )
    return calls


def _order(**over):
    base = dict(
        id="order-uuid",
        order_number="MM-20260925-001",
        email="sara@example.com",
        customer_name="Sara Ali",
        customer_phone="+971501234567",
        total=Decimal("125.00"),
        items=[SimpleNamespace(quantity=2, product_name="Cookie Melt")],
        payment_transactions=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── the signature ─────────────────────────────────────────────────────────────


def test_the_concatenation_is_paymobs_own_worked_example():
    obj = _obj(
        amount_cents=100000,
        created_at="2024-06-13T11:33:44.592345",
        currency="EGP",
        id=192036465,
        integration_id=4097558,
        order={"id": 217503754},
        owner=302852,
    )
    joined = "".join(
        pm._hmac_text(pm._signed_value(obj, f, flat=False))
        for f in pm._TRANSACTION_HMAC_FIELDS
    )
    assert joined == DOC_CONCATENATION


def test_the_signature_is_hex_sha512_of_that_string():
    obj = _obj()
    joined = "".join(
        pm._hmac_text(pm._signed_value(obj, f, flat=False))
        for f in pm._TRANSACTION_HMAC_FIELDS
    )
    expected = hmac.new(
        HMAC_SECRET.encode(), joined.encode(), hashlib.sha512
    ).hexdigest()
    assert _sign(obj) == expected


def test_a_valid_post_is_accepted():
    body, query = _post(_obj())
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.event_type is PaymentEventType.SUCCEEDED


def test_the_redirect_query_verifies_with_the_same_fields():
    event = pm.provider.parse_return(_flat(_obj()))
    assert event.event_type is PaymentEventType.SUCCEEDED
    assert event.session_id == "ord_217503754"


def test_the_redirect_accepts_order_id_as_well_as_order():
    flat = _flat(_obj())
    flat["order_id"] = flat.pop("order")
    assert pm.provider.parse_return(flat).session_id == "ord_217503754"


def test_a_null_pan_signs_as_empty():
    """A wallet payment has no PAN. `str(None)` would be "None" and never match."""
    obj = _obj(source_data={"pan": None, "type": "wallet", "sub_type": None})
    body, query = _post(obj)
    assert pm.provider.parse_webhook(body, {}, query=query).event_type is (
        PaymentEventType.SUCCEEDED
    )


@pytest.mark.parametrize("presented", ["", "0" * 128, "deadbeef"])
def test_a_forged_or_missing_signature_is_refused(presented):
    body, _ = _post(_obj())
    with pytest.raises(BadRequestError, match="(?i)signature"):
        pm.provider.parse_webhook(
            body, {}, query={"hmac": presented} if presented else {}
        )


def test_tampering_with_a_signed_field_breaks_the_signature():
    obj = _obj()
    body, query = _post(obj)
    tampered = json.loads(body)
    tampered["obj"]["amount_cents"] = 1
    with pytest.raises(BadRequestError, match="(?i)signature"):
        pm.provider.parse_webhook(json.dumps(tampered).encode(), {}, query=query)


def test_no_secret_configured_refuses_everything(monkeypatch):
    monkeypatch.setattr(settings, "PAYMOB_HMAC_SECRET", "")
    body, query = _post(_obj())
    with pytest.raises(BadRequestError, match="(?i)signature"):
        pm.provider.parse_webhook(body, {}, query=query)


def test_a_token_callback_verifies_and_does_nothing():
    obj = {
        "card_subtype": "MasterCard",
        "created_at": "2026-09-25T11:33:44",
        "email": "sara@example.com",
        "id": 77,
        "masked_pan": "xxxx-2346",
        "merchant_id": 1,
        "order_id": 217503754,
        "token": "tok_x",
    }
    signature = pm._expected_hmac([obj[f] for f in pm._TOKEN_HMAC_FIELDS], HMAC_SECRET)
    body = json.dumps({"type": "TOKEN", "obj": obj}).encode()
    event = pm.provider.parse_webhook(body, {}, query={"hmac": signature})
    assert event.event_type is PaymentEventType.UNHANDLED
    assert event.event_id == "paymob:token:77"


# ── only signed fields decide ─────────────────────────────────────────────────


def test_the_order_is_found_by_the_signed_paymob_order_id_not_extras():
    """`extras` is unsigned; a replayed signature with a forged `extras` must not
    retarget the event. So the event names no order number at all."""
    obj = _obj(payment_key_claims={"extra": {"order_number": "MM-SOMEONE-ELSE"}})
    body, query = _post(obj)
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.order_number is None
    assert event.session_id == "ord_217503754"


def test_a_transaction_from_a_foreign_integration_is_ignored():
    """Test and live integration ids differ, and the id is signed — `is_live`
    is not. A test payment arriving at a live endpoint confirms nothing."""
    body, query = _post(_obj(integration_id=999))
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.event_type is PaymentEventType.UNHANDLED
    assert event.raw_type == "foreign_integration"


def test_a_non_aed_success_is_not_applied():
    body, query = _post(_obj(currency="EGP"))
    assert pm.provider.parse_webhook(body, {}, query=query).event_type is (
        PaymentEventType.UNHANDLED
    )


def test_handles_are_namespaced():
    body, query = _post(_obj())
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.payment_id == "txn_192036465"
    assert event.session_id == "ord_217503754"
    assert event.amount_captured == 12500
    assert event.event_id == "paymob:txn:192036465:ok"


def test_the_post_and_the_redirect_dedupe_against_each_other():
    obj = _obj()
    body, query = _post(obj)
    posted = pm.provider.parse_webhook(body, {}, query=query)
    returned = pm.provider.parse_return(_flat(obj))
    assert posted.event_id == returned.event_id


# ── what each transaction means ───────────────────────────────────────────────


def test_a_decline_carries_no_payment_handle_and_a_reason():
    obj = _obj(
        success=False,
        data={
            "message": "Insufficient funds",
            "txn_response_code": "DECLINED",
            "acq_response_code": "51",
        },
    )
    body, query = _post(obj)
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.event_type is PaymentEventType.FAILED
    assert event.payment_id is None, (
        "a declined transaction must never be refunded against"
    )
    assert event.session_id == "ord_217503754"
    assert event.failure_reason is PaymentFailureReason.INSUFFICIENT_FUNDS
    assert event.error_code == "51"
    assert event.error_message == "Insufficient funds"


@pytest.mark.parametrize(
    ("acq", "error_occured", "expected"),
    [
        ("54", False, PaymentFailureReason.EXPIRED_CARD),
        ("05", False, PaymentFailureReason.CARD_DECLINED),
        ("ZZ", False, PaymentFailureReason.CARD_DECLINED),
        ("ZZ", True, PaymentFailureReason.PROCESSING_ERROR),
    ],
)
def test_decline_codes_bucket(acq, error_occured, expected):
    obj = _obj(
        success=False,
        error_occured=error_occured,
        data={"message": "x", "acq_response_code": acq},
    )
    body, query = _post(obj)
    assert pm.provider.parse_webhook(body, {}, query=query).failure_reason is expected


def test_a_failed_3ds_is_authentication_required():
    obj = _obj(
        success=False,
        data={
            "message": "3DS failed",
            "migs_order": {"authenticationStatus": "AUTHENTICATION_FAILED"},
        },
    )
    body, query = _post(obj)
    assert pm.provider.parse_webhook(body, {}, query=query).failure_reason is (
        PaymentFailureReason.AUTHENTICATION_REQUIRED
    )


def test_pending_is_acknowledged_and_applied_to_nothing():
    body, query = _post(_obj(pending=True, success=False))
    assert pm.provider.parse_webhook(body, {}, query=query).event_type is (
        PaymentEventType.UNHANDLED
    )


def test_a_child_refund_transaction_is_not_counted():
    """The refund transaction itself. The parent's own callback carries the
    money; counting both would count one refund twice."""
    body, query = _post(_obj(has_parent_transaction=True, amount_cents=5000))
    assert pm.provider.parse_webhook(body, {}, query=query).event_type is (
        PaymentEventType.UNHANDLED
    )


def test_a_refunded_parent_is_a_cumulative_refund():
    body, query = _post(_obj(is_refunded=True, refunded_amount_cents=5000))
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.event_type is PaymentEventType.REFUNDED
    assert event.cumulative is True
    assert event.amount_refunded == 5000
    assert event.fully_refunded is False
    assert event.refund_id == "paymob:txn_192036465:5000"


def test_a_voided_payment_is_a_full_refund():
    body, query = _post(_obj(is_voided=True))
    event = pm.provider.parse_webhook(body, {}, query=query)
    assert event.event_type is PaymentEventType.REFUNDED
    assert event.fully_refunded is True
    assert event.amount_refunded == 12500


async def test_the_unsigned_refunded_amount_is_re_read_before_it_is_believed(
    monkeypatch,
):
    """`refunded_amount_cents` is outside the signature. A replay claiming the
    whole order was refunded is corrected by asking Paymob."""

    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        assert request.url.path == "/api/acceptance/transactions/192036465"
        return httpx.Response(
            200,
            json={
                "refunded_amount_cents": 3000,
                "amount_cents": 12500,
                "order": {"id": 217503754},
            },
        )

    _transport(monkeypatch, handler)
    body, query = _post(_obj(is_refunded=True, refunded_amount_cents=12500))
    parsed = pm.provider.parse_webhook(body, {}, query=query)
    verified = await pm.provider.verify_event(parsed)
    assert verified.amount_refunded == 3000
    assert verified.fully_refunded is False
    assert verified.event_id == "paymob:txn:192036465:refunded-3000"
    assert verified.refund_id == "paymob:txn_192036465:3000"


def _reads(monkeypatch, record: dict):
    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        return httpx.Response(200, json=record)

    return _transport(monkeypatch, handler)


async def test_a_success_is_applied_only_once_paymob_confirms_it(monkeypatch):
    _reads(monkeypatch, _obj())
    body, query = _post(_obj())
    parsed = pm.provider.parse_webhook(body, {}, query=query)
    assert await pm.provider.verify_event(parsed) is parsed


async def test_a_callback_paymob_does_not_recognise_is_not_applied(monkeypatch):
    """Paymob's own record says this transaction belongs to another order."""
    _reads(monkeypatch, _obj(order={"id": 1}))
    body, query = _post(_obj())
    verified = await pm.provider.verify_event(
        pm.provider.parse_webhook(body, {}, query=query)
    )
    assert verified.event_type is PaymentEventType.UNHANDLED
    assert verified.raw_type == "mismatch"


async def test_a_claimed_success_paymob_records_as_declined_is_not_applied(monkeypatch):
    _reads(monkeypatch, _obj(success=False))
    body, query = _post(_obj())
    verified = await pm.provider.verify_event(
        pm.provider.parse_webhook(body, {}, query=query)
    )
    assert verified.event_type is PaymentEventType.UNHANDLED


async def test_digits_moved_between_order_and_owner_still_verify_but_are_caught(
    monkeypatch,
):
    """
    Paymob's HMAC has no separators, so `order=217503754, owner=302852` and
    `order=2175037543, owner=02852` sign identically. The shape check rejects
    the leading-zero owner; a split that survives it is caught by Paymob's own
    record of the transaction naming the original order.
    """
    genuine = _obj(owner=312852)
    _, query = _post(genuine)
    shifted = _obj(order={"id": 2175037543}, owner=12852)
    body = json.dumps({"type": "TRANSACTION", "obj": shifted}).encode()
    parsed = pm.provider.parse_webhook(body, {}, query=query)  # the signature holds
    assert parsed.session_id == "ord_2175037543"

    _reads(monkeypatch, genuine)
    verified = await pm.provider.verify_event(parsed)
    assert verified.event_type is PaymentEventType.UNHANDLED


def test_a_leading_zero_split_is_refused_outright():
    genuine = _obj()
    _, query = _post(genuine)
    shifted = _obj(order={"id": 2175037543}, owner="02852")
    body = json.dumps({"type": "TRANSACTION", "obj": shifted}).encode()
    with pytest.raises(BadRequestError, match="Malformed"):
        pm.provider.parse_webhook(body, {}, query=query)


async def test_verify_leaves_unhandled_events_alone(monkeypatch):
    _transport(monkeypatch, lambda r: pytest.fail("nothing to confirm"))
    body, query = _post(_obj(pending=True, success=False))
    parsed = pm.provider.parse_webhook(body, {}, query=query)
    assert await pm.provider.verify_event(parsed) is parsed


# ── creating the checkout ─────────────────────────────────────────────────────


async def test_create_session_sends_one_line_for_the_whole_total(monkeypatch):
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        sent["auth"] = request.headers["authorization"]
        return httpx.Response(
            201,
            json={
                "id": "pi_test_1",
                "intention_order_id": 265715202,
                "client_secret": "are_csk_test_1",
                "status": "intended",
            },
        )

    _transport(monkeypatch, handler)
    session = await pm.provider.create_session(_order())
    body = sent["body"]

    assert sent["auth"] == "Token sk_test_x"
    assert body["amount"] == 12500
    assert body["currency"] == "AED"
    assert body["payment_methods"] == [CARD_ID]
    assert sum(item["amount"] for item in body["items"]) == body["amount"]
    assert body["billing_data"]["first_name"] == "Sara"
    assert body["billing_data"]["last_name"] == "Ali"
    assert body["billing_data"]["phone_number"] == "+971501234567"
    assert body["expiration"] == settings.PAYMOB_CHECKOUT_EXPIRY_SECONDS
    assert (
        body["notification_url"]
        == "https://api.example.test/api/v1/payments/webhooks/paymob"
    )
    assert (
        body["redirection_url"]
        == "https://api.example.test/api/v1/payments/paymob/return"
    )
    assert body["special_reference"].startswith("MM-20260925-001-")

    assert session.session_id == "ord_265715202"
    assert session.payment_id is None, "no payment exists until the customer pays"
    query = parse_qs(urlparse(session.checkout_url).query)
    assert query == {"publicKey": ["pk_test_x"], "clientSecret": ["are_csk_test_1"]}


async def test_each_attempt_gets_its_own_reference(monkeypatch):
    refs = []

    def handler(request):
        refs.append(json.loads(request.content)["special_reference"])
        return httpx.Response(
            201,
            json={"intention_order_id": len(refs), "client_secret": f"cs_{len(refs)}"},
        )

    _transport(monkeypatch, handler)
    await pm.provider.create_session(_order())
    await pm.provider.create_session(_order())
    assert refs[0] != refs[1]


async def test_a_nameless_phoneless_guest_still_gets_valid_billing_data(monkeypatch):
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(
            201, json={"intention_order_id": 1, "client_secret": "cs"}
        )

    _transport(monkeypatch, handler)
    await pm.provider.create_session(
        _order(customer_name=None, customer_phone=None, email="guest@example.com")
    )
    billing = sent["billing_data"]
    assert billing["first_name"] == "guest"
    assert billing["last_name"] == "NA"
    assert billing["phone_number"]


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
async def test_a_paymob_outage_fails_over(monkeypatch, status_code):
    _transport(monkeypatch, lambda r: httpx.Response(status_code, text="down"))
    with pytest.raises(GatewayUnavailableError):
        await pm.provider.create_session(_order())


async def test_an_unreachable_paymob_fails_over(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("slow", request=request)

    _transport(monkeypatch, handler)
    with pytest.raises(GatewayUnavailableError):
        await pm.provider.create_session(_order())


async def test_a_refusal_is_not_re_presented_elsewhere(monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(400, json={"detail": "bad phone"}))
    with pytest.raises(BadRequestError):
        await pm.provider.create_session(_order())


async def test_a_201_with_no_client_secret_is_the_gateway_unwell(monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(201, json={"id": "pi_1"}))
    with pytest.raises(GatewayUnavailableError):
        await pm.provider.create_session(_order())


def test_unconfigured_by_default(monkeypatch):
    monkeypatch.setattr(settings, "PAYMOB_ENABLED", False)
    assert pm.provider.is_configured() is False


@pytest.mark.parametrize(
    "missing",
    [
        "PAYMOB_SECRET_KEY",
        "PAYMOB_PUBLIC_KEY",
        "PAYMOB_API_KEY",
        "PAYMOB_HMAC_SECRET",
        "PAYMOB_CARD_INTEGRATION_ID",
        "PAYMOB_CALLBACK_BASE_URL",
    ],
)
def test_every_credential_is_required(monkeypatch, missing):
    monkeypatch.setattr(settings, missing, 0 if missing.endswith("_ID") else "")
    assert pm.provider.is_configured() is False


# ── refunds ───────────────────────────────────────────────────────────────────


def _refund_backend(remote_refunded: int, *, refund_response=None, posted=None):
    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        if request.url.path.startswith("/api/acceptance/transactions/"):
            return httpx.Response(200, json={"refunded_amount_cents": remote_refunded})
        if request.url.path == "/api/acceptance/void_refund/refund":
            if posted is not None:
                posted.append(json.loads(request.content))
            return refund_response or httpx.Response(
                200,
                json={
                    "id": 574600,
                    "success": True,
                    "pending": False,
                    "amount_cents": json.loads(request.content)["amount_cents"],
                    "is_refund": True,
                },
            )
        raise AssertionError(f"unexpected call {request.url}")

    return handler


async def test_a_refund_is_sent_when_nothing_is_unaccounted_for(monkeypatch):
    posted: list = []
    _transport(monkeypatch, _refund_backend(2000, posted=posted))
    result = await pm.provider.refund(
        payment_id="txn_192036465",
        amount=Decimal("30.00"),
        idempotency_key="refund-MM-1-50.00",
        expected_prior_refunded=Decimal("20.00"),
    )
    assert posted == [{"transaction_id": 192036465, "amount_cents": 3000}]
    assert result.amount == Decimal("30.00")
    assert result.status == "completed"
    # The id the parent's refund callback will produce: cumulative 20 + 30.
    assert result.refund_id == "paymob:txn_192036465:5000"


async def test_a_retry_of_a_refund_that_already_landed_is_not_sent_again(monkeypatch):
    """The rolled-back-transaction case the idempotency key exists for: Paymob
    already shows this exact amount refunded beyond our books."""
    posted: list = []
    _transport(monkeypatch, _refund_backend(5000, posted=posted))
    result = await pm.provider.refund(
        payment_id="txn_192036465",
        amount=Decimal("30.00"),
        idempotency_key="refund-MM-1-50.00",
        expected_prior_refunded=Decimal("20.00"),
    )
    assert posted == [], "the customer must not be refunded twice"
    assert result.amount == Decimal("30.00")
    assert result.refund_id == "paymob:txn_192036465:5000"


async def test_an_unexplained_refund_on_paymob_blocks_a_new_one(monkeypatch):
    """A dashboard refund whose callback has not landed. Booking it as this
    request's result would skip what the admin asked for; sending on top might
    overpay. Refuse and say why."""
    posted: list = []
    _transport(monkeypatch, _refund_backend(1000, posted=posted))
    with pytest.raises(BadRequestError, match="Reconcile"):
        await pm.provider.refund(
            payment_id="txn_192036465",
            amount=Decimal("30.00"),
            idempotency_key="k",
            expected_prior_refunded=Decimal("0"),
        )
    assert posted == []


async def test_a_declined_refund_is_a_refusal(monkeypatch):
    _transport(
        monkeypatch,
        _refund_backend(
            0,
            refund_response=httpx.Response(
                200,
                json={
                    "success": False,
                    "pending": False,
                    "data": {"message": "No balance"},
                },
            ),
        ),
    )
    with pytest.raises(BadRequestError, match="No balance"):
        await pm.provider.refund(
            payment_id="txn_1",
            amount=Decimal("10"),
            idempotency_key="k",
            expected_prior_refunded=Decimal("0"),
        )


async def test_a_refund_timeout_is_retryable(monkeypatch):
    def handler(request):
        if request.url.path == "/api/acceptance/void_refund/refund":
            raise httpx.ReadTimeout("slow", request=request)
        return _refund_backend(0)(request)

    _transport(monkeypatch, handler)
    with pytest.raises(GatewayUnavailableError):
        await pm.provider.refund(
            payment_id="txn_1",
            amount=Decimal("10"),
            idempotency_key="k",
            expected_prior_refunded=Decimal("0"),
        )


async def test_a_refund_never_accepts_a_foreign_handle():
    with pytest.raises(BadRequestError):
        await pm.provider.refund(
            payment_id="pi_stripe_1", amount=Decimal("10"), idempotency_key="k"
        )


# ── resume and reconcile ──────────────────────────────────────────────────────


def _attempt(**over):
    base = dict(
        gateway="paymob",
        status="pending",
        session_id="ord_265715202",
        checkout_url="https://uae.checkout.paymob.test/?publicKey=pk_test_x&clientSecret=cs_live_1",
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_resume_offers_a_still_open_hosted_intention(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/intention/element/pk_test_x/cs_live_1/"
        return httpx.Response(200, json={"status": "intended", "confirmed": False})

    _transport(monkeypatch, handler)
    order = _order(payment_transactions=[_attempt()])
    assert await pm.provider.resume_url(order) == _attempt().checkout_url


async def test_resume_skips_a_confirmed_intention(monkeypatch):
    _transport(
        monkeypatch,
        lambda r: httpx.Response(200, json={"status": "intended", "confirmed": True}),
    )
    assert (
        await pm.provider.resume_url(_order(payment_transactions=[_attempt()])) is None
    )


async def test_resume_never_offers_an_apple_pay_only_intention(monkeypatch):
    _transport(monkeypatch, lambda r: pytest.fail("nothing to ask about"))
    order = _order(payment_transactions=[_attempt(checkout_url="")])
    assert await pm.provider.resume_url(order) is None


async def test_resume_never_raises(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    _transport(monkeypatch, handler)
    assert (
        await pm.provider.resume_url(_order(payment_transactions=[_attempt()])) is None
    )


async def test_fetch_outcome_reports_a_success_the_webhook_would_have(monkeypatch):
    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        assert request.url.path == "/api/ecommerce/orders/transaction_inquiry"
        assert json.loads(request.content)["order_id"] == "265715202"
        return httpx.Response(
            200, json=_obj(order={"id": 265715202}, id=900, integration_id=CARD_ID)
        )

    _transport(monkeypatch, handler)
    event = await pm.provider.fetch_outcome(_attempt())
    assert event.event_type is PaymentEventType.SUCCEEDED
    # The same id the webhook for this transaction carries, so they dedupe.
    assert event.event_id == "paymob:txn:900:ok"
    assert event.session_id == "ord_265715202"


async def test_fetch_outcome_ignores_an_answer_about_another_order(monkeypatch):
    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        return httpx.Response(200, json=_obj(order={"id": 1}))

    _transport(monkeypatch, handler)
    assert await pm.provider.fetch_outcome(_attempt()) is None


async def test_fetch_outcome_is_nothing_for_another_gateway():
    assert await pm.provider.fetch_outcome(_attempt(gateway="stripe")) is None


@pytest.mark.parametrize("status_code", [401, 403, 404])
async def test_a_broken_paymob_configuration_fails_over(monkeypatch, status_code):
    """A rotated key or a retired integration id is nothing the customer did —
    card checkout should move to the next gateway, not go down."""
    _transport(monkeypatch, lambda r: httpx.Response(status_code, json={"detail": "x"}))
    with pytest.raises(GatewayUnavailableError):
        await pm.provider.create_session(_order())


async def test_a_refund_reply_can_never_book_more_than_was_asked(monkeypatch):
    """If Paymob answered with the parent (amount 12500) for a 30.00 partial,
    believing it would book the whole charge as refunded."""
    _transport(
        monkeypatch,
        _refund_backend(
            0,
            refund_response=httpx.Response(
                200, json={"success": True, "pending": False, "amount_cents": 12500}
            ),
        ),
    )
    result = await pm.provider.refund(
        payment_id="txn_1",
        amount=Decimal("30.00"),
        idempotency_key="k",
        expected_prior_refunded=Decimal("0"),
    )
    assert result.amount == Decimal("30.00")
    assert result.refund_id == "paymob:txn_1:3000"


async def test_an_inquiry_paymob_refuses_means_no_payment(monkeypatch):
    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        return httpx.Response(404, json={"detail": "Not found."})

    _transport(monkeypatch, handler)
    assert await pm.provider.fetch_outcome(_attempt()) is None


async def test_an_inquiry_outage_is_still_maybe(monkeypatch):
    def handler(request):
        if request.url.path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer_x"})
        return httpx.Response(503, text="down")

    _transport(monkeypatch, handler)
    with pytest.raises(GatewayUnavailableError):
        await pm.provider.fetch_outcome(_attempt())
