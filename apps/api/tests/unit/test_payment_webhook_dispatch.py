"""
One webhook path for every gateway.

The claim this file exists to hold is that `payment_service` acts on
`PaymentEventType` and never on who sent it — so a Ziina success and a Stripe
success take literally the same code, and adding a third processor adds a
provider and nothing here.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.order import OrderStatusEnum
from app.models.payment_transaction import PaymentTransactionStatusEnum
from app.services import payment_service
from app.services.providers.base import GatewayEvent, PaymentEventType


class _Provider:
    def __init__(self, event: GatewayEvent | Exception):
        self._event = event

    def parse_webhook(self, payload, headers):
        if isinstance(self._event, Exception):
            raise self._event
        return self._event


@pytest.fixture
def order():
    return SimpleNamespace(
        id="order-uuid",
        order_number="MM-20260808-001",
        email="c@example.com",
        total=Decimal("125.00"),
        status=OrderStatusEnum.CREATED,
        payment_provider="ziina",
        payment_method="card",
        payment_id="pi_ziina_1",
        payment_transactions=[],
        # A refund webhook now records how much went back, whoever issued it —
        # including a refund made by hand in a gateway dashboard, which used to
        # move the status and leave these untouched.
        refunded_amount=Decimal("0"),
        refunded_at=None,
        # A confirmation now also puts the order on its branch's register, so
        # the stub carries the columns that path reads. Publishing itself is
        # stubbed in `wired` — it is the same call whichever gateway sent the
        # event, which is exactly what this file is not about.
        source="online",
        branch_id="branch-uuid",
        check_number=None,
    )


@pytest.fixture
def db():
    session = MagicMock()
    # The dedup INSERT ... ON CONFLICT: rowcount 1 means "not seen before".
    session.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    session.flush = AsyncMock()
    return session


@pytest.fixture
def wired(monkeypatch, order):
    """A `payment_service` whose order lookup and emails are stubbed out."""
    monkeypatch.setattr(payment_service, "_load_order", AsyncMock(return_value=order))
    monkeypatch.setattr(
        payment_service, "_load_order_by_handle", AsyncMock(return_value=order)
    )
    monkeypatch.setattr(
        payment_service.order_service, "to_response", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        payment_service.order_service, "publish_to_register", AsyncMock()
    )
    sent = SimpleNamespace(
        confirmation=AsyncMock(),
        owner=AsyncMock(),
        failed=AsyncMock(),
        refund=AsyncMock(),
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_order_confirmation", sent.confirmation
    )
    monkeypatch.setattr(
        payment_service.email_service,
        "send_owner_order_notification",
        sent.owner,
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_payment_failed", sent.failed
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_refund_notification", sent.refund
    )
    return sent


def _register(monkeypatch, gateway: str, event):
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "PROVIDERS",
        {gateway: _Provider(event)},
    )


def _event(event_type, **kw):
    return GatewayEvent(
        event_id=kw.pop("event_id", "evt_1"),
        event_type=event_type,
        raw_type=kw.pop("raw_type", "some.event"),
        **kw,
    )


# ── the same code for both gateways ───────────────────────────────────────────


@pytest.mark.parametrize("gateway", ["stripe", "ziina"])
async def test_a_success_confirms_the_order_whoever_sent_it(
    db, order, wired, monkeypatch, gateway
):
    _register(
        monkeypatch,
        gateway,
        _event(PaymentEventType.SUCCEEDED, payment_id="pi_1"),
    )

    result = await payment_service.handle_webhook(db, gateway, b"{}", {})

    assert result["received"] is True
    assert order.status == OrderStatusEnum.CONFIRMED
    assert order.payment_id == "pi_1"
    wired.confirmation.assert_awaited_once()
    wired.owner.assert_awaited_once()


@pytest.mark.parametrize("gateway", ["stripe", "ziina"])
async def test_a_failure_marks_it_failed_whoever_sent_it(
    db, order, wired, monkeypatch, gateway
):
    _register(monkeypatch, gateway, _event(PaymentEventType.FAILED))

    await payment_service.handle_webhook(db, gateway, b"{}", {})

    assert order.status == OrderStatusEnum.PAYMENT_FAILED
    wired.failed.assert_awaited_once()


async def test_an_unhandled_event_touches_nothing(db, order, wired, monkeypatch):
    """
    Ziina pushes a status for every transition, most of which are the middle of
    a payment. Acting on `pending` would confirm an order against money that has
    not moved.
    """
    _register(monkeypatch, "ziina", _event(PaymentEventType.UNHANDLED))

    await payment_service.handle_webhook(db, "ziina", b"{}", {})

    assert order.status == OrderStatusEnum.CREATED
    wired.confirmation.assert_not_awaited()


# ── dedup ─────────────────────────────────────────────────────────────────────


async def test_a_duplicate_delivery_is_a_no_op(db, order, wired, monkeypatch):
    """
    Ziina retries three times on a non-2xx. Without this the retry of a success
    sends the customer a second confirmation email.
    """
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    _register(monkeypatch, "ziina", _event(PaymentEventType.SUCCEEDED))

    result = await payment_service.handle_webhook(db, "ziina", b"{}", {})

    assert result["duplicate"] is True
    assert order.status == OrderStatusEnum.CREATED
    wired.confirmation.assert_not_awaited()
    # Still identifiable in the audit log — a duplicate is a row worth having,
    # and one that says only "duplicate: true" cannot be tied to anything.
    assert result["event_type"] == "some.event"
    # And `matched` stays absent, so the column is null rather than a `false`
    # that would read as "we should have found an order and did not".
    assert "matched" not in result


async def test_an_event_with_no_id_is_refused_rather_than_processed(
    db, order, wired, monkeypatch
):
    """
    No ID means no dedup, and no dedup means a retry double-sends. A gateway
    that issues none must synthesise one — refusing here is what stops a future
    provider quietly skipping that.
    """
    _register(monkeypatch, "ziina", _event(PaymentEventType.SUCCEEDED, event_id=""))

    with pytest.raises(BadRequestError, match="event id"):
        await payment_service.handle_webhook(db, "ziina", b"{}", {})


async def test_an_unknown_gateway_is_not_acknowledged(db, monkeypatch):
    monkeypatch.setattr(payment_service.payment_gateway_router, "PROVIDERS", {})

    with pytest.raises(NotFoundError):
        await payment_service.handle_webhook(db, "paypal", b"{}", {})


# ── correlation ───────────────────────────────────────────────────────────────


async def test_metadata_is_preferred_when_the_gateway_carries_it(
    db, order, wired, monkeypatch
):
    _register(
        monkeypatch,
        "stripe",
        _event(PaymentEventType.SUCCEEDED, order_number="MM-20260808-001"),
    )

    await payment_service.handle_webhook(db, "stripe", b"{}", {})

    payment_service._load_order.assert_awaited_once()
    payment_service._load_order_by_handle.assert_not_awaited()


async def test_the_handle_is_the_fallback_for_a_gateway_with_no_metadata(
    db, order, wired, monkeypatch
):
    """
    Ziina's create-payment-intent body has nine fields and none of them holds an
    order number, so the handle we stored is the only link back.
    """
    _register(
        monkeypatch,
        "ziina",
        _event(PaymentEventType.SUCCEEDED, payment_id="pi_ziina_1"),
    )

    await payment_service.handle_webhook(db, "ziina", b"{}", {})

    payment_service._load_order_by_handle.assert_awaited_once()
    assert payment_service._load_order_by_handle.await_args.args[1] == "ziina"


class TestTheHandleLookupIsScopedByGateway:
    """
    Both processors mint ids beginning `pi_`.

    An unscoped lookup would let a Ziina event land on a Stripe order that
    happened to share a handle — confirming an order nobody paid for, mailing
    the customer about it, and putting a cake in the kitchen. Vanishingly
    unlikely, and not a coin worth flipping on this particular code path.

    Both queries in `_load_order_by_handle` are checked, because the second one
    — the fallback for orders written before `payment_transactions` existed —
    was the one that was missing it.
    """

    @staticmethod
    def _statements(db):
        return [str(call.args[0]) for call in db.execute.await_args_list]

    async def _lookup(self, gateway: str):
        db = MagicMock()
        empty = MagicMock()
        empty.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=empty)
        with pytest.raises(NotFoundError):
            await payment_service._load_order_by_handle(
                db, gateway, payment_id="pi_ambiguous"
            )
        return self._statements(db)

    async def test_the_transaction_query_names_the_gateway(self):
        transactions_query, _legacy = await self._lookup("ziina")
        assert "payment_transactions.gateway" in transactions_query

    async def test_the_legacy_query_names_the_gateway_too(self):
        _transactions, legacy = await self._lookup("ziina")
        assert "orders.payment_provider" in legacy

    async def test_a_null_provider_counts_as_stripe_and_only_stripe(self):
        """
        Orders carrying a bare `pi_…` and no provider predate the second
        gateway, so they are Stripe's — and must not be reachable from a Ziina
        event, which is precisely the collision being guarded against.
        """
        _t, stripe_legacy = await self._lookup("stripe")
        assert "IS NULL" in stripe_legacy.upper()

        _t, ziina_legacy = await self._lookup("ziina")
        assert "IS NULL" not in ziina_legacy.upper()


async def test_an_unplaceable_success_is_logged_not_raised(
    db, wired, monkeypatch, caplog
):
    """
    Money moved with no order to attach it to. Raising would make the gateway
    retry a lookup that fails identically forever — three times, on Ziina, and
    then never again — so it is recorded loudly and acknowledged.
    """
    monkeypatch.setattr(
        payment_service,
        "_load_order_by_handle",
        AsyncMock(side_effect=NotFoundError("nope")),
    )
    _register(
        monkeypatch, "ziina", _event(PaymentEventType.SUCCEEDED, payment_id="pi_x")
    )

    result = await payment_service.handle_webhook(db, "ziina", b"{}", {})

    assert result["received"] is True
    assert "manual reconciliation required" in caplog.text


# ── the transaction row keeps up ──────────────────────────────────────────────


async def test_a_stripe_attempt_learns_its_payment_handle_on_confirmation(
    db, order, wired, monkeypatch
):
    """
    Stripe's Payment Intent does not exist until the customer pays, so the
    session row is written with `payment_id` null and fills it in here.
    """
    attempt = SimpleNamespace(
        gateway="stripe",
        session_id="cs_1",
        payment_id=None,
        status=PaymentTransactionStatusEnum.PENDING.value,
        raw_status=None,
        error_code=None,
        error_message=None,
        is_settled=False,
    )
    order.payment_transactions = [attempt]
    _register(
        monkeypatch,
        "stripe",
        _event(
            PaymentEventType.SUCCEEDED,
            order_number="MM-20260808-001",
            session_id="cs_1",
            payment_id="pi_1",
        ),
    )

    await payment_service.handle_webhook(db, "stripe", b"{}", {})

    assert attempt.payment_id == "pi_1"
    assert attempt.status == PaymentTransactionStatusEnum.SUCCEEDED.value


async def test_a_late_cancel_does_not_unsettle_a_paid_attempt(
    db, order, wired, monkeypatch
):
    """
    Gateways reorder deliveries, and Ziina emits a status per transition with no
    ordering guarantee at all. A cancel arriving after a success is a late
    duplicate, not a reversal — only a refund or a dispute actually undoes one.
    """
    attempt = SimpleNamespace(
        gateway="ziina",
        session_id="pi_z1",
        payment_id="pi_z1",
        status=PaymentTransactionStatusEnum.SUCCEEDED.value,
        raw_status=None,
        error_code=None,
        error_message=None,
        is_settled=True,
    )
    order.payment_transactions = [attempt]
    order.status = OrderStatusEnum.CONFIRMED
    _register(
        monkeypatch,
        "ziina",
        _event(PaymentEventType.CANCELLED, payment_id="pi_z1"),
    )

    await payment_service.handle_webhook(db, "ziina", b"{}", {})

    assert attempt.status == PaymentTransactionStatusEnum.SUCCEEDED.value


# ── refunds ───────────────────────────────────────────────────────────────────


async def test_a_partial_refund_leaves_the_order_alone(db, order, wired, monkeypatch):
    """
    Knocking AED 5 off a damaged box used to mark the whole order refunded, drop
    it out of the fulfilment views, and email the customer that their money was
    on its way back.
    """
    order.status = OrderStatusEnum.CONFIRMED
    _register(
        monkeypatch,
        "stripe",
        _event(
            PaymentEventType.REFUNDED,
            order_number="MM-20260808-001",
            amount_refunded=500,
            amount_captured=12500,
            fully_refunded=False,
        ),
    )

    await payment_service.handle_webhook(db, "stripe", b"{}", {})

    assert order.status == OrderStatusEnum.CONFIRMED
    wired.refund.assert_not_awaited()


async def test_a_ziina_refund_is_measured_against_the_order_total(
    db, order, wired, monkeypatch
):
    """
    Ziina's refund object carries what was refunded and never what was
    originally captured, so the only thing left to compare against is what we
    charged — which is a number we hold.
    """
    order.status = OrderStatusEnum.CONFIRMED
    _register(
        monkeypatch,
        "ziina",
        _event(
            PaymentEventType.REFUNDED,
            payment_id="pi_ziina_1",
            amount_refunded=12500,  # the whole AED 125.00
        ),
    )

    await payment_service.handle_webhook(db, "ziina", b"{}", {})

    assert order.status == OrderStatusEnum.REFUNDED
    wired.refund.assert_awaited_once()


async def test_a_partial_ziina_refund_still_leaves_the_order_alone(
    db, order, wired, monkeypatch
):
    order.status = OrderStatusEnum.CONFIRMED
    _register(
        monkeypatch,
        "ziina",
        _event(PaymentEventType.REFUNDED, payment_id="pi_ziina_1", amount_refunded=500),
    )

    await payment_service.handle_webhook(db, "ziina", b"{}", {})

    assert order.status == OrderStatusEnum.CONFIRMED
