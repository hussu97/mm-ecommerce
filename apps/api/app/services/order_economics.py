"""
What the shop actually keeps on one order.

The order screen showed what the customer paid and, separately, what the courier
cost. The number nobody could see was the one that decides whether the order was
worth taking: a 45-dirham basket with a 24-dirham van and a 2.30 processing fee
nets 18.70, and working that out meant opening a Stripe dashboard in another tab
and doing arithmetic on a phone.

**Two percentages, because they answer different questions.** Against the total
including fees, it says how much of everything the customer handed over survives
— the number that matters when deciding whether to run free delivery at all.
Against the item value alone, it says what the cake earns once delivery is
stripped out — the number that matters when pricing the cake. Showing one of
them invites the other to be guessed at.

Everything here is an estimate and the response says so. Stripe's actual fee
lives on the charge's balance transaction and does not exist until it settles;
the courier's actual cost is what they invoiced, which for a batched order is a
share of a run. Both are the best figure available at the moment somebody looks,
and both are more useful than a blank.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.models.payment_gateway import PaymentGateway

logger = logging.getLogger(__name__)

__all__ = ["OrderEconomics", "for_order", "processing_fee"]

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0))


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class OrderEconomics:
    """One order's money, from what was charged to what is left."""

    #: What the customer paid, fees included. The card figure.
    charged: Decimal
    #: What they paid for goods — `charged` less delivery and low-order fees.
    #: The denominator of the second percentage, and the base a refund uses.
    items_value: Decimal
    #: What the courier cost us, where a courier was involved and has told us.
    #: A batched order carries its share of the run rather than a whole booking.
    courier_cost: Decimal | None
    #: What the processor keeps. Estimated from the gateway's rate unless the
    #: gateway has reported the real number.
    processing_fee: Decimal
    #: Whether `processing_fee` is the rate or the invoice.
    processing_fee_is_estimated: bool
    #: Anything sent back to the customer. Comes off the top: a refunded order
    #: nets what is left after the money returned, and the fees on a refunded
    #: charge are usually *not* returned by the processor.
    refunded: Decimal

    @property
    def net(self) -> Decimal:
        """What the shop keeps."""
        return _round(
            self.charged
            - (self.courier_cost or _ZERO)
            - self.processing_fee
            - self.refunded
        )

    @property
    def margin_on_charged(self) -> Decimal | None:
        """Net as a share of everything the customer handed over."""
        return self._share_of(self.charged)

    @property
    def margin_on_items(self) -> Decimal | None:
        """
        Net as a share of the goods alone.

        Frequently the more useful of the two and frequently the uglier: on a
        free-delivery order the courier cost is carried entirely by the cake,
        and this is where that shows.
        """
        return self._share_of(self.items_value)

    def _share_of(self, base: Decimal) -> Decimal | None:
        # A zero base is a zero-total order — a full-discount staff order, a
        # replacement sent out for free. There is no percentage of nothing, and
        # returning 0 would read as "we kept none of it" rather than "the
        # question does not apply".
        if base <= 0:
            return None
        return _round(self.net / base * 100)


def processing_fee(
    charged: Decimal, gateway: PaymentGateway | None
) -> tuple[Decimal, bool]:
    """
    What the processor keeps on this charge, and whether it is a guess.

    Falls back to 2.9% + AED 1 when the gateway row cannot be found — the rate
    both processors charge today. A missing row is a seeding problem rather than
    a free transaction, and showing a fee of zero would flatter every order on
    the screen.
    """
    if charged <= 0:
        return _ZERO, False
    percent = _money(gateway.fee_percent) if gateway else Decimal("2.9")
    fixed = _money(gateway.fee_fixed) if gateway else Decimal("1")
    return _round(charged * percent / 100 + fixed), True


async def for_order(db: AsyncSession, order: Order) -> OrderEconomics:
    """
    Assemble one order's economics from what we know right now.

    Never raises and never blocks the screen: a missing delivery row, an
    unknown gateway or a courier that has not invoiced yet each leave one field
    softer rather than taking the page down. The admin reads this beside the
    order it describes, and a 500 there is worse than an estimate.
    """
    charged = _money(order.total)
    fees = _money(order.delivery_fee) + _money(order.low_order_fee)
    items_value = max(charged - fees, _ZERO)

    delivery = (
        await db.execute(
            select(OrderDelivery).where(OrderDelivery.order_id == order.id)
        )
    ).scalar_one_or_none()
    # `cost_total` is what the courier actually charged; `quoted_cost` is what
    # they said they would. The first is the truth and arrives late, so the
    # second stands in until it does. Null on a third-party zone, where nobody
    # invoices us per order, and that is a genuine "we do not know" rather than
    # a zero — a third party's van is not free, it is just not itemised here.
    courier_cost = None
    if delivery is not None:
        raw = (
            delivery.cost_total
            if delivery.cost_total is not None
            else delivery.quoted_cost
        )
        courier_cost = _money(raw) if raw is not None else None

    gateway = None
    if order.payment_provider:
        gateway = (
            await db.execute(
                select(PaymentGateway).where(
                    PaymentGateway.code == order.payment_provider
                )
            )
        ).scalar_one_or_none()

    # A cash order pays no processor. The money is in a drawer, and charging it
    # a Stripe fee on this screen would make every counter sale look worse than
    # it is.
    if order.payment_method != "card":
        fee, estimated = _ZERO, False
    else:
        fee, estimated = processing_fee(charged, gateway)

    return OrderEconomics(
        charged=_round(charged),
        items_value=_round(items_value),
        courier_cost=_round(courier_cost) if courier_cost is not None else None,
        processing_fee=fee,
        processing_fee_is_estimated=estimated,
        refunded=_round(_money(order.refunded_amount)),
    )
