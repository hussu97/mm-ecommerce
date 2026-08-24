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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, to_decimal
from app.models.courier import Courier
from app.models.courier_branch_rate import CourierBranchRate
from app.models.order import Order
from app.models.payment_gateway import PaymentGateway
from app.services.couriers import courier_catalog
from app.services.orders.order_pricing import VAT_RATE

logger = logging.getLogger(__name__)

__all__ = ["OrderFees", "compute", "stamp"]

_ZERO = Decimal("0")

#: The card processor's published rate, used when the `payment_gateways` row for
#: an order's provider cannot be found. Both processors charge this today. A
#: missing row is a seeding problem rather than a free transaction, and a fee of
#: zero would flatter every card order on the screen.
_DEFAULT_CARD_PERCENT = Decimal("2.9")
_DEFAULT_CARD_FIXED = Decimal("1")


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
    return money(before_tax * (Decimal("1") + VAT_RATE))


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
    charged = to_decimal(order.total)
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

    Two things bend the plain reading of the row, because the contracts are not
    all the same sentence:

      * **The branch.** Deliveroo charges 27% in Sharjah and 31% in Barsha, so a
        `courier_branch_rate` override is consulted first and its non-null
        numbers win over the courier default.
      * **The grammar flags** on the courier row — VAT already inside the rate
        (Keeta), the flat part netted out before the percentage (Keeta), the
        payment fee waived on cash (Careem), the flat part charged only to a
        member (Careem Plus, Talabat Pro). A courier that sets none of them
        behaves exactly as it did before they existed.
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

    override = None
    if order.branch_id is not None:
        override = (
            await db.execute(
                select(CourierBranchRate).where(
                    CourierBranchRate.courier_id == row.id,
                    CourierBranchRate.branch_id == order.branch_id,
                )
            )
        ).scalar_one_or_none()

    return OrderFees(
        aggregator_fee=_commission(charged, order, row, override),
        payment_fee=_payment_fee(charged, order, row, override),
        payment_fee_is_estimated=True,
    )


def _override_or(override: object, courier_value: object, field: str) -> object:
    """The branch override's value for `field` if it set one, else the courier's.

    Null on the override means "no special rate here", never "free" — so it
    falls through to the courier default, and only a value the branch actually
    typed replaces it.
    """
    if override is not None:
        value = getattr(override, field)
        if value is not None:
            return value
    return courier_value


def _commission(
    charged: Decimal, order: Order, row: Courier, override: object
) -> Decimal | None:
    """The marketplace's cut on this order, its grammar flags applied."""
    percent = _override_or(override, row.commission_percent, "commission_percent")
    fixed = _override_or(override, row.commission_fixed, "commission_fixed")

    # The flat part is a member's fee on Careem/Talabat, and we cannot see who
    # is a member — so unless the order is explicitly flagged, the flat part is
    # not charged. Dropped to None (not zero) so that a percentage-only contract
    # is unaffected and a flat-only one correctly reads as unknown.
    if row.commission_fixed_requires_member and not order.aggregator_customer_is_member:
        fixed = None

    if percent is None and fixed is None:
        return None

    if row.commission_fixed_net_of_base and percent is not None and fixed is not None:
        # Keeta: "4 AED + 25% of (the item value − the original 4 AED)".
        before_tax = to_decimal(fixed) + _as_fraction(to_decimal(percent)) * (
            charged - to_decimal(fixed)
        )
    else:
        before_tax = charged * _as_fraction(to_decimal(percent)) + to_decimal(fixed)

    return money(before_tax) if row.commission_vat_inclusive else _with_vat(before_tax)


def _payment_fee(
    charged: Decimal, order: Order, row: Courier, override: object
) -> Decimal | None:
    """What the marketplace's card handling cost on this order."""
    # A cash order took no card, so a cash-exempt contract (Careem) charges
    # nothing — a true zero, the way a cash counter sale pays no Stripe fee, not
    # an unknown. `postpaid` is cash; a null payment type is treated as card
    # (the historical default and the common case) rather than exempting it.
    if row.payment_fee_cash_exempt and order.aggregator_payment_type == "postpaid":
        return _ZERO

    percent = _override_or(override, row.payment_fee_percent, "payment_fee_percent")
    fixed = _override_or(override, row.payment_fee_fixed, "payment_fee_fixed")
    if percent is None and fixed is None:
        return None

    before_tax = charged * _as_fraction(to_decimal(percent)) + to_decimal(fixed)
    return money(before_tax) if row.payment_fee_vat_inclusive else _with_vat(before_tax)


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
        to_decimal(gateway.fee_percent)
        if gateway is not None
        else _DEFAULT_CARD_PERCENT
    )
    fixed = (
        to_decimal(gateway.fee_fixed) if gateway is not None else _DEFAULT_CARD_FIXED
    )
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
