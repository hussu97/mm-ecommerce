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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, to_decimal
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.models.payment_gateway import PaymentGateway
from app.services.orders.order_pricing import VAT_RATE

logger = logging.getLogger(__name__)

__all__ = [
    "DIRECT_COST_THRESHOLD",
    "OrderEconomics",
    "for_order",
    "processing_fee",
]

_ZERO = Decimal("0")

#: The share of menu-price goods an order has to keep to be worth taking.
#:
#: Not a law of accounting — it is the shop's own rule of thumb for whether an
#: order covered its direct costs, and it is the line the orders list draws its
#: tick and cross against. A constant rather than a setting because it is a
#: single number that has never had a second opinion; the day it needs one it
#: belongs on `business_settings`, not scattered through call sites.
DIRECT_COST_THRESHOLD = Decimal("50")


@dataclass(frozen=True)
class OrderEconomics:
    """One order's money, from what was charged to what is left."""

    #: What the customer paid, fees included. The card figure.
    charged: Decimal
    #: What they paid for goods — `charged` less delivery and low-order fees.
    #: The denominator of the second percentage, and the base a refund uses.
    items_value: Decimal
    #: The goods at menu price, before any discount. The denominator of the
    #: direct-cost check below — a discount is a cost the shop chose to bear,
    #: so measuring against what was actually charged would hide exactly the
    #: orders the check exists to find.
    items_before_discount: Decimal
    #: What the courier cost us, where a courier was involved and has told us.
    #: A batched order carries its share of the run rather than a whole booking.
    courier_cost: Decimal | None
    #: What the marketplace took, on an aggregator order. Null on a website or
    #: counter order (there is no marketplace) and on an aggregator whose rate
    #: nobody has configured yet — see `order_fees`.
    #:
    #: This is the aggregator's analogue of `courier_cost`: an aggregator order
    #: never has one, because MM dispatches nothing and is invoiced for no van.
    #: Its cost of sale is the commission instead, and until this existed an
    #: aggregator order showed no cost of sale at all.
    aggregator_fee: Decimal | None
    #: What the processor keeps. Estimated from the gateway's rate unless the
    #: gateway has reported the real number.
    processing_fee: Decimal
    #: Whether `processing_fee` is the rate or the invoice.
    processing_fee_is_estimated: bool
    #: Anything sent back to the customer. Comes off the top: a refunded order
    #: nets what is left after the money returned, and the fees on a refunded
    #: charge are usually *not* returned by the processor.
    refunded: Decimal
    #: Whether this order should have had a cost of sale at all — somebody
    #: carried it, or a marketplace sold it. False for a counter sale, which is
    #: handed across a counter and rightly shows neither.
    #:
    #: Read only by `covers_direct_cost`, to tell "no cost of sale" apart from
    #: "a cost of sale we have not been told about".
    expects_cost_of_sale: bool = False

    @property
    def net(self) -> Decimal:
        """What the shop keeps."""
        return money(
            self.charged
            - (self.courier_cost or _ZERO)
            - (self.aggregator_fee or _ZERO)
            - self.processing_fee
            - self.refunded
        )

    @property
    def cost_cover(self) -> Decimal | None:
        """
        Net as a share of the goods at menu price.

        The question the shop actually asks of a list of orders: *is this one
        paying for itself?* Every other percentage here is measured against
        something the order's own discounts and fees moved, so a heavily
        discounted order can post a healthy-looking margin on a basket that was
        half price. This one holds the denominator still.

        Null when there were no goods — a pure delivery-fee adjustment, a
        zero-total replacement — because there is no share of nothing.
        """
        return self._share_of(self.items_before_discount)

    @property
    def covers_direct_cost(self) -> bool | None:
        """
        Whether this order cleared the bar, or None if we cannot say.

        Three-valued on purpose, and the third value is the useful one. An order
        whose commission rate is not configured yet has an unknowable net, and
        answering `False` would put it in the same column as an order that is
        genuinely losing money — which is how a rate that nobody ever filled in
        becomes a fortnight of investigating the wrong orders.
        """
        if self.aggregator_fee is None and self.courier_cost is None:
            # Neither cost of sale is known. A counter sale legitimately has
            # neither — it is handed over the counter — so the test is whether
            # this order was *supposed* to have one.
            if self.expects_cost_of_sale:
                return None
        share = self.cost_cover
        return None if share is None else share >= DIRECT_COST_THRESHOLD

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
        return money(self.net / base * 100)


def processing_fee(
    charged: Decimal, gateway: PaymentGateway | None
) -> tuple[Decimal, bool]:
    """
    What the processor keeps on this charge, and whether it is a guess.

    Falls back to 2.9% + AED 1 when the gateway row cannot be found — the rate
    both processors charge today. A missing row is a seeding problem rather than
    a free transaction, and showing a fee of zero would flatter every order on
    the screen.

    **VAT is charged on the fee, and the shop pays it.** A processor's fee is a
    service sold to the shop, so the invoice is the fee plus 5% on top of it —
    the screen was reporting the fee before tax and quietly overstating what
    every card order nets by that 5%. On a 54-dirham order the difference is 13
    fils; across a year of them it is the sort of number that makes a margin
    report disagree with a bank statement for reasons nobody can find.

    This is the shop's own VAT rate, the same one the order's prices are
    inclusive of, so it is read from `order_pricing` rather than written out a
    second time here. Note the direction: the order's VAT is *inclusive* — it
    is already inside the price the customer paid — while this one is *added*,
    because the fee is quoted before tax.
    """
    if charged <= 0:
        return _ZERO, False
    percent = to_decimal(gateway.fee_percent) if gateway else Decimal("2.9")
    fixed = to_decimal(gateway.fee_fixed) if gateway else Decimal("1")
    before_tax = charged * _as_fraction(percent) + fixed
    return money(before_tax * (Decimal("1") + VAT_RATE)), True


def _as_fraction(percent: Decimal) -> Decimal:
    """
    A `_percent` column turned into something you can multiply by.

    Named rather than an inline `/ 100` because the codebase carries both
    conventions in columns that look alike: `payment_gateways.fee_percent` is
    `2.9` and `orders.vat_rate` is `0.0500`, and the only thing distinguishing
    them is the suffix (see the note on `Order.vat_rate`). A bare `/ 100` beside
    a rate multiplication is exactly where the wrong one gets copied — and both
    mistakes produce a plausible-looking number, off by 100x, on a screen the
    shop reads its margins from.
    """
    return percent / 100


async def for_order(db: AsyncSession, order: Order) -> OrderEconomics:
    """
    Assemble one order's economics from what we know right now.

    Never raises and never blocks the screen: a missing delivery row, an
    unknown gateway or a courier that has not invoiced yet each leave one field
    softer rather than taking the page down. The admin reads this beside the
    order it describes, and a 500 there is worse than an estimate.
    """
    charged = to_decimal(order.total)
    fees = to_decimal(order.delivery_fee) + to_decimal(order.low_order_fee)
    items_value = max(charged - fees, _ZERO)
    # The goods at menu price. `subtotal` is written before any discount comes
    # off (see `order_pricing.compute`), which is exactly what the direct-cost
    # check wants: a 40% coupon should show up as a cost, not disappear into a
    # smaller denominator.
    items_before_discount = to_decimal(order.subtotal)

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
        courier_cost = to_decimal(raw) if raw is not None else None

    # ── The two fees, preferring what was written down ───────────────────────
    #
    # `orders.payment_fee` and `orders.aggregator_fee` are stamped by
    # `order_fees` when the total goes final, and they are the truth: they hold
    # the rate that applied on the day, so renegotiating a contract cannot
    # rewrite the margin on orders the shop has already taken.
    #
    # The fallback below is for the rows that predate the stamp and for the
    # narrow window between an order being created and its fees being written.
    # It reproduces what this module did before the columns existed, so an
    # unstamped row still shows a number rather than a blank.
    is_aggregator = (order.source or "") == "aggregator"

    if order.payment_fee is not None:
        fee, estimated = to_decimal(order.payment_fee), True
    elif is_aggregator:
        # An aggregator order whose channel has no configured rate. Unknown, and
        # said so rather than guessed at — a marketplace charges *something* for
        # taking the card, and zero here would be a claim we cannot make.
        fee, estimated = _ZERO, True
    elif order.payment_method != "card":
        # A cash order pays no processor. The money is in a drawer, and charging
        # it a Stripe fee on this screen would make every counter sale look
        # worse than it is.
        fee, estimated = _ZERO, False
    else:
        gateway = None
        if order.payment_provider:
            gateway = (
                await db.execute(
                    select(PaymentGateway).where(
                        PaymentGateway.code == order.payment_provider
                    )
                )
            ).scalar_one_or_none()
        fee, estimated = processing_fee(charged, gateway)

    aggregator_fee = (
        to_decimal(order.aggregator_fee) if order.aggregator_fee is not None else None
    )

    return OrderEconomics(
        charged=money(charged),
        items_value=money(items_value),
        items_before_discount=money(items_before_discount),
        courier_cost=money(courier_cost) if courier_cost is not None else None,
        aggregator_fee=money(aggregator_fee) if aggregator_fee is not None else None,
        processing_fee=fee,
        processing_fee_is_estimated=estimated,
        refunded=money(to_decimal(order.refunded_amount)),
        # A marketplace sold it, or a courier carried it. Either way somebody
        # took a cut and the order screen should say so — even when the figure
        # itself is still unknown.
        expects_cost_of_sale=is_aggregator or delivery is not None,
    )
