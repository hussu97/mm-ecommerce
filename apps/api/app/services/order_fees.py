"""
What an order costs us before anybody counts the flour.

Two deductions arrive with the order rather than with the cake: whoever carried
it takes a cut, and whoever took the money takes a fee. Until now neither was
written down. `order_economics` worked the card fee out afresh every time an
admin opened an order, and the marketplace's commission was not modelled at all
— so a Talabat order and a website order sat in the same table looking equally
profitable, and one of them was quietly twenty-five percent lighter.

**This module computes; it does not decide when.** `stamp` writes the two
columns and every caller reaches for that at the moment the total is final —
ingest for an aggregator order, checkout for a website one, close for a counter
sale. Recomputing on read was the old behaviour and it is what made a
profit-and-loss impossible: there was nothing to sum and no record of the rate
that applied on the day.

**Rates are configuration, not code.** A marketplace's commission is a
commercial figure that changes when somebody renegotiates, so it lives on the
`couriers` row for that channel and is edited in the console. Only Noon Food's
is agreed today (25% + 2%, both before VAT); every other aggregator is null
until the shop supplies one.

**A fee is a pair, not a number.** Each of the two is a percentage of the basket
*plus* a flat amount, because that is how the contracts are written — "25% plus
two dirhams an order" — and it is the same shape a card processor's fee has
always had here (`payment_gateways.fee_percent` + `fee_fixed`). Either half may
be null; only both being null means the fee is unknown.

**Null propagates and that is the feature.** An aggregator with no rate yields a
null fee, a null net and a screen that says "not itemised". The alternative —
treating an unknown rate as zero — makes a Talabat order look like it kept every
dirham, which is the exact mistake this module exists to stop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courier import Courier
from app.models.order import Order
from app.models.payment_gateway import PaymentGateway
from app.services import courier_catalog
from app.services.order_pricing import VAT_RATE

logger = logging.getLogger(__name__)

__all__ = ["OrderFees", "compute", "stamp"]

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")

#: The card processor's published rate, used when the `payment_gateways` row for
#: an order's provider cannot be found. Both processors charge this today. A
#: missing row is a seeding problem rather than a free transaction, and a fee of
#: zero would flatter every card order on the screen.
_DEFAULT_CARD_PERCENT = Decimal("2.9")
_DEFAULT_CARD_FIXED = Decimal("1")


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0))


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _with_vat(before_tax: Decimal) -> Decimal:
    """
    A fee grossed up by the VAT charged on it.

    Note the direction, because the codebase carries both and they look alike:
    an order's own VAT is *inclusive* — already inside the price the customer
    paid — while a fee billed **to** the shop is quoted before tax and has 5%
    added. Reading one as the other understates every fee by that 5%, which is
    thirteen fils on a fifty-dirham order and a reconciliation nobody can close
    across a year of them.
    """
    return _round(before_tax * (Decimal("1") + VAT_RATE))


def _as_fraction(percent: Decimal) -> Decimal:
    """
    A `_percent` column turned into something you can multiply by.

    Named rather than an inline `/ 100` for the reason set out on
    `Order.vat_rate`: this codebase holds `2.9` and `0.0500` in columns four
    characters apart, and a bare `/ 100` beside a rate multiplication is exactly
    where the wrong one gets copied. Both mistakes produce a plausible number,
    off by a hundred, on a screen the shop reads its margins from.
    """
    return percent / 100


@dataclass(frozen=True)
class OrderFees:
    """The two deductions that arrive with an order, VAT included."""

    #: The marketplace's cut. Null on a website or counter order (there is no
    #: marketplace) and on an aggregator whose rate is not configured yet.
    aggregator_fee: Decimal | None
    #: What taking the money cost — the processor on a card order, the
    #: marketplace on an aggregator one. Zero on cash; null when unknowable.
    payment_fee: Decimal | None
    #: Whether `payment_fee` is a published rate rather than an invoice. True
    #: for everything today: Stripe's real figure lives on the charge's balance
    #: transaction and does not exist until it settles, and a marketplace
    #: reports nothing per order at all.
    payment_fee_is_estimated: bool


async def compute(db: AsyncSession, order: Order) -> OrderFees:
    """
    Work out both fees for one order from the rates in force right now.

    Never raises: a missing courier row or gateway row leaves one figure softer
    rather than taking down the path that called it. Ingest and checkout both
    reach here, and neither should fail because a rate has not been seeded.
    """
    charged = _money(order.total)
    if charged <= 0:
        # A zero-total order — a full-discount staff order, a replacement sent
        # out for free. Nobody took a cut of nothing.
        return OrderFees(
            aggregator_fee=_ZERO if _is_aggregator(order) else None,
            payment_fee=_ZERO,
            payment_fee_is_estimated=False,
        )

    if _is_aggregator(order):
        return await _aggregator_fees(db, order, charged)
    return await _own_channel_fees(db, order, charged)


def _is_aggregator(order: Order) -> bool:
    return (order.source or "") == "aggregator"


async def _aggregator_fees(
    db: AsyncSession, order: Order, charged: Decimal
) -> OrderFees:
    """
    A marketplace order: commission and payment fee, both from the channel's row.

    Charged against `total`, which for an aggregator order is the basket the
    marketplace collected on our behalf. Their delivery charge is deliberately
    not in it (`orders.aggregator_delivery_fee` carries that, receipt-only), so
    there is no risk of paying commission on a fee we never received.
    """
    code = courier_catalog.code_for_channel(order.aggregator_channel)
    row = None
    if code:
        row = (
            await db.execute(select(Courier).where(Courier.code == code))
        ).scalar_one_or_none()
    if row is None:
        # An unrecognised channel name, or a channel with no `couriers` row.
        # Both are "we cannot say", and both are worth a line in the log —
        # a new marketplace should not silently price itself at nothing.
        logger.warning(
            "No courier row for aggregator channel %r on order %s; fees left unknown",
            order.aggregator_channel,
            order.order_number,
        )
        return OrderFees(None, None, True)

    return OrderFees(
        aggregator_fee=_rate_pair(
            charged, row.commission_percent, row.commission_fixed
        ),
        payment_fee=_rate_pair(charged, row.payment_fee_percent, row.payment_fee_fixed),
        payment_fee_is_estimated=True,
    )


def _rate_pair(charged: Decimal, percent: object, fixed: object) -> Decimal | None:
    """
    One fee from its two halves — a share of the basket plus a flat amount.

    Both contracts and card processors are quoted this way ("25% plus two
    dirhams", "2.9% + AED 1"), so a fee is a pair rather than a number, and the
    pair is what decides whether it is known.

    **Unknown only when both halves are null.** One half set and the other null
    is a contract that quotes only a percentage, or only a flat amount, and the
    missing half is genuinely nothing — reading that as "unknown" would blank
    the net on every channel whose contract happens to be simple.
    """
    if percent is None and fixed is None:
        return None
    before_tax = charged * _as_fraction(_money(percent)) + _money(fixed)
    return _with_vat(before_tax)


async def _own_channel_fees(
    db: AsyncSession, order: Order, charged: Decimal
) -> OrderFees:
    """
    A website or counter order: no marketplace, and a card fee only if a card
    was used.

    A cash order pays no processor — the money is in a drawer — and charging it
    a Stripe fee would make every counter sale look worse than it is.
    """
    if order.payment_method != "card":
        return OrderFees(None, _ZERO, False)

    gateway = None
    if order.payment_provider:
        gateway = (
            await db.execute(
                select(PaymentGateway).where(
                    PaymentGateway.code == order.payment_provider
                )
            )
        ).scalar_one_or_none()

    percent = (
        _money(gateway.fee_percent) if gateway is not None else _DEFAULT_CARD_PERCENT
    )
    fixed = _money(gateway.fee_fixed) if gateway is not None else _DEFAULT_CARD_FIXED
    return OrderFees(
        aggregator_fee=None,
        payment_fee=_with_vat(charged * _as_fraction(percent) + fixed),
        payment_fee_is_estimated=True,
    )


async def stamp(db: AsyncSession, order: Order) -> OrderFees:
    """
    Write both fees onto the order, at the moment its total is final.

    Callers are the three places a total stops moving: aggregator ingest,
    website checkout, and closing a counter check. Idempotent — running it again
    on an unchanged order writes the same numbers — so a retried ingest or a
    reopened-and-reclosed check costs nothing.

    Flushes rather than commits, per the transaction convention: the
    request-scoped `get_db` dependency owns the commit.
    """
    fees = await compute(db, order)
    order.aggregator_fee = fees.aggregator_fee
    order.payment_fee = fees.payment_fee
    await db.flush()
    return fees
