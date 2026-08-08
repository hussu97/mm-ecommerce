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
from app.services import payment_service
from app.services.payment_gateway_router import GatewayChoice
from app.services.providers.base import GatewaySession, GatewayUnavailableError


class _Provider:
    def __init__(self, code, *, raises=None):
        self.code = code
        self._raises = raises
        self.calls = 0

    def is_configured(self):
        return True

    def create_session(self, order, *, test_mode=False):
        self.calls += 1
        if self._raises:
            raise self._raises
        return GatewaySession(
            session_id=f"{self.code}_sess", checkout_url=f"https://{self.code}/pay"
        )


def _choice(code, *, raises=None, supports_failover=True):
    return GatewayChoice(
        row=PaymentGateway(
            code=code,
            name=code,
            is_active=True,
            priority=1,
            supports_failover=supports_failover,
            test_mode=False,
        ),
        provider=_Provider(code, raises=raises),
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


async def test_a_refusal_is_still_recorded_before_it_propagates(db, order, monkeypatch):
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
