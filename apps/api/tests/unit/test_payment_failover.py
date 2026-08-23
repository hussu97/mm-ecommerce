"""
What happens to a checkout when a processor is having a bad day.

This is the behaviour the whole feature was built for, so it is worth stating
plainly what "working" means: a customer whose Stripe session cannot be created
gets a Ziina one instead and never knows, and a customer whose *card* was
refused gets told, once, by the processor that refused it.

The two are told apart by exception type — `GatewayUnavailableError` versus
anything else — and that distinction is the only thing standing between
"resilient" and "re-presents declined cards until one goes through".
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError
from app.models.order import OrderStatusEnum
from app.models.payment_gateway import PaymentGateway
from app.models.payment_transaction import PaymentTransactionStatusEnum
from app.services.payments import payment_service
from app.services.payments.payment_gateway_router import GatewayChoice
from app.services.providers.base import GatewaySession, GatewayUnavailableError


class _Provider:
    def __init__(self, code, *, raises=None, session_id=None):
        self.code = code
        self._raises = raises
        # Stripe hands back the *same* session id for a repeat call on one
        # order — it is called with a per-order idempotency key. That is the
        # behaviour the retry test below depends on.
        self.session_id = session_id or f"{code}_sess"
        self.calls = 0

    def is_configured(self):
        return True

    async def create_session(self, order, *, test_mode=False):
        self.calls += 1
        if self._raises:
            raise self._raises
        return GatewaySession(
            session_id=self.session_id, checkout_url=f"https://{self.code}/pay"
        )


def _choice(code, *, raises=None, supports_failover=True, session_id=None):
    return GatewayChoice(
        row=PaymentGateway(
            code=code,
            name=code,
            is_active=True,
            priority=1,
            supports_failover=supports_failover,
            test_mode=False,
        ),
        provider=_Provider(code, raises=raises, session_id=session_id),
    )


@pytest.fixture
def order():
    return SimpleNamespace(
        id="order-uuid",
        order_number="MM-20260808-001",
        email="c@example.com",
        total=Decimal("125.00"),
        status=OrderStatusEnum.CREATED,
        payment_method=None,
        payment_provider=None,
        payment_id=None,
        payment_transactions=[],
    )


@pytest.fixture
def db():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _added_transactions(db):
    return [call.args[0] for call in db.add.call_args_list]


async def test_a_healthy_primary_is_used_and_nothing_else_is_touched(
    db, order, monkeypatch
):
    options = [_choice("stripe"), _choice("ziina")]
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(return_value=options),
    )

    result = await payment_service._create_card_session(db, order, order.total)

    assert result["provider"] == "stripe"
    assert options[1].provider.calls == 0
    assert order.payment_provider == "stripe"
    # The method is the customer's word, and it is never a gateway's name.
    assert order.payment_method == "card"


async def test_an_outage_on_the_primary_falls_over_silently(db, order, monkeypatch):
    options = [
        _choice("stripe", raises=GatewayUnavailableError("502 from Stripe")),
        _choice("ziina"),
    ]
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(return_value=options),
    )

    result = await payment_service._create_card_session(db, order, order.total)

    assert result["provider"] == "ziina"
    assert result["checkout_url"] == "https://ziina/pay"
    assert order.payment_provider == "ziina"


async def test_the_abandoned_attempt_leaves_a_row_saying_why(db, order, monkeypatch):
    """
    "Which gateway did this order try, in what order, and what did each one
    say" is the first question anyone asks about a checkout that misbehaved.
    Before `payment_transactions` it had no answer at all.
    """
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(
            return_value=[
                _choice("stripe", raises=GatewayUnavailableError("502 from Stripe")),
                _choice("ziina"),
            ]
        ),
    )

    await payment_service._create_card_session(db, order, order.total)

    failed, succeeded = _added_transactions(db)
    assert failed.gateway == "stripe"
    assert failed.status == PaymentTransactionStatusEnum.FAILED.value
    assert failed.error_code == "gateway_unavailable"
    assert "502 from Stripe" in failed.error_message
    assert succeeded.gateway == "ziina"
    assert succeeded.session_id == "ziina_sess"


async def test_a_refused_card_is_never_re_presented(db, order, monkeypatch):
    """
    The safety property. A `BadRequestError` is the processor's *opinion* about
    this payment, and asking a second one is how a single honest decline becomes
    two — or worse, becomes a charge on a gateway nobody selected.
    """
    options = [
        _choice("stripe", raises=BadRequestError("Your card was declined")),
        _choice("ziina"),
    ]
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(return_value=options),
    )

    with pytest.raises(BadRequestError, match="declined"):
        await payment_service._create_card_session(db, order, order.total)

    assert options[1].provider.calls == 0


async def test_a_refusal_is_written_down_even_though_it_will_not_survive(
    db, order, monkeypatch
):
    """
    The row is added, and `get_db` will roll it back on the way out — which is
    correct, not a bug. A request that ends in an exception did nothing, and the
    database should not claim otherwise.

    Pinned anyway, because the row *does* survive when the request survives: a
    failover that eventually finds a working gateway commits the whole set, and
    "Stripe was tried first and 502'd" is then on the order. The two cases share
    this code and only differ in how the request ends.

    An earlier version of this test was named "…is still recorded before it
    propagates" and its comment claimed the attempt outlived the exception. It
    did not, and the mock session it ran against could not have told anyone.
    """
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(return_value=[_choice("stripe", raises=BadRequestError("declined"))]),
    )

    with pytest.raises(BadRequestError):
        await payment_service._create_card_session(db, order, order.total)

    (attempt,) = _added_transactions(db)
    assert attempt.status == PaymentTransactionStatusEnum.FAILED.value
    assert attempt.error_code == "refused"
    # No session id, which is what keeps a run of these clear of
    # `uq_payment_transactions_gateway_session`. On a bad day there are several.
    assert attempt.session_id is None


async def test_a_failed_attempt_carries_no_session_id(db, order, monkeypatch):
    """
    Several failures for one order and one gateway must be insertable.

    The unique index on `(gateway, session_id)` is partial — `WHERE session_id
    IS NOT NULL` — so attempts that never got a session do not collide with each
    other. Losing that would turn a second outage on the same order into a 500.
    """
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(
            return_value=[
                _choice("stripe", raises=GatewayUnavailableError("502")),
                _choice("ziina", raises=GatewayUnavailableError("503")),
            ]
        ),
    )

    with pytest.raises(BadRequestError):
        await payment_service._create_card_session(db, order, order.total)

    attempts = _added_transactions(db)
    assert len(attempts) == 2
    assert all(a.session_id is None for a in attempts)


class TestRetryingAPayment:
    """
    The regression that would have broken a live flow.

    Stripe is called with `idempotency_key=f"sess_{order_number}"`, so a second
    `create_session` for the same order inside 24 hours returns the *same*
    Checkout Session — the same `cs_…`. Before this was handled, the retry added
    a second `payment_transactions` row carrying that id, violated
    `uq_payment_transactions_gateway_session`, and handed a 500 to the customer
    whose card had just been declined and who had pressed "try again".

    Reproduced against a real Postgres during review; the mock session used by
    the rest of this file cannot enforce an index, so these assert the
    behaviour that keeps the index satisfied rather than the index itself.
    """

    async def test_a_repeat_session_reuses_its_row(self, db, order, monkeypatch):
        monkeypatch.setattr(
            payment_service.payment_gateway_router,
            "candidates",
            AsyncMock(return_value=[_choice("stripe", session_id="cs_same")]),
        )

        await payment_service._create_card_session(db, order, order.total)
        first = list(order.payment_transactions)
        await payment_service._create_card_session(db, order, order.total)

        assert len(order.payment_transactions) == 1, "a second row would collide"
        assert order.payment_transactions[0] is first[0]
        assert order.payment_transactions[0].session_id == "cs_same"

    async def test_a_genuinely_new_session_gets_its_own_row(
        self, db, order, monkeypatch
    ):
        """
        Ziina takes no idempotency key, so every retry is a new payment intent
        and deserves its own row. Reusing one here would lose the earlier
        intent's id — which is the only handle a Ziina webhook can be matched on.
        """
        monkeypatch.setattr(
            payment_service.payment_gateway_router,
            "candidates",
            AsyncMock(return_value=[_choice("ziina", session_id="pi_first")]),
        )
        await payment_service._create_card_session(db, order, order.total)

        monkeypatch.setattr(
            payment_service.payment_gateway_router,
            "candidates",
            AsyncMock(return_value=[_choice("ziina", session_id="pi_second")]),
        )
        await payment_service._create_card_session(db, order, order.total)

        assert [t.session_id for t in order.payment_transactions] == [
            "pi_first",
            "pi_second",
        ]

    async def test_the_same_id_on_a_different_gateway_is_a_different_attempt(
        self, db, order, monkeypatch
    ):
        """
        Both processors mint ids beginning `pi_`. The index is scoped by
        gateway, and so is the reuse — otherwise a Ziina intent could be written
        onto a Stripe attempt.
        """
        monkeypatch.setattr(
            payment_service.payment_gateway_router,
            "candidates",
            AsyncMock(return_value=[_choice("stripe", session_id="pi_collide")]),
        )
        await payment_service._create_card_session(db, order, order.total)

        monkeypatch.setattr(
            payment_service.payment_gateway_router,
            "candidates",
            AsyncMock(return_value=[_choice("ziina", session_id="pi_collide")]),
        )
        await payment_service._create_card_session(db, order, order.total)

        assert [t.gateway for t in order.payment_transactions] == ["stripe", "ziina"]


async def test_every_gateway_down_is_an_apology_not_a_stack_trace(
    db, order, monkeypatch
):
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(
            return_value=[
                _choice("stripe", raises=GatewayUnavailableError("down")),
                _choice("ziina", raises=GatewayUnavailableError("also down")),
            ]
        ),
    )

    with pytest.raises(BadRequestError, match="temporarily unavailable"):
        await payment_service._create_card_session(db, order, order.total)

    assert len(_added_transactions(db)) == 2


async def test_a_gateway_that_opted_out_is_not_reached_for_automatically(
    db, order, monkeypatch
):
    options = [
        _choice("stripe", raises=GatewayUnavailableError("down")),
        _choice("ziina", supports_failover=False),
    ]
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "candidates",
        AsyncMock(return_value=options),
    )

    with pytest.raises(BadRequestError, match="temporarily unavailable"):
        await payment_service._create_card_session(db, order, order.total)

    assert options[1].provider.calls == 0
