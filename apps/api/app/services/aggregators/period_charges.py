"""The marketplace charges no order carries, for the P&L — dated by statement.

A marketplace bills some costs per period rather than per order: Deliveroo's
AED 200/outlet monthly platform fee (and its correction credits), noon's monthly
platform fee and long-distance fee, a Keeta subscription. They arrive as
settlement lines with no `external_order_id`, so no order's P&L can hold them,
and they are booked here at the **statement line's date** — the date the
marketplace put the charge on its statement. That is the only date every
channel gives (a Deliveroo line's free-text "for the period July 1 to July 31"
is not parseable enough to trust), it is the date the shop can reconcile
against, and a monthly fee lands once a month either way.

**noon double-books two fees, and this is where that is undone.** noon's Tax
Invoice overview carries a statement-level payment fee and cancellation fee
(`noon_provider._OVERVIEW_SUMMARY_FEES`), while its order feed *also* stamps a
per-order payment fee (`orderPostpaidFee`) and cancellation fee on some orders —
a partial view of the same money (Sept 8–15: 46.87 on orders vs 164.01
invoiced). Summing both would double-count what the orders carry; ignoring the
statement would drop the rest. So for those two categories the charge here is
the **true-up**: what the statement invoiced less what the statement's own
orders already carry, so order-level plus period-level equals the invoice.

Amounts come back as billed (VAT included) with the VAT inside them beside it,
like every P&L cost line:
Careem/Deliveroo/Talabat itemise fee VAT on its own line, while Keeta and noon
bill VAT-inclusive and the 5% is peeled out here (the same treatment as the Fees
& VAT roll-up in `api/v1/aggregators`). The aggregator entity is VAT-registered,
so that VAT is reclaimable input VAT.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, to_decimal
from app.models.aggregator import (
    CHANNEL_KEETA,
    CHANNEL_NOON,
    AggregatorOrder,
    AggregatorStatementLine,
)
from app.models.order import Order
from app.services.aggregators import statement_categories as cats
from app.services.orders.order_pnl import in_pnl_clause
from app.services.orders.order_pricing import VAT_RATE

__all__ = ["PeriodCharge", "pnl_channel_for", "period_charges"]

#: Channels whose fee lines are stored VAT-inclusive with no separate VAT line.
_VAT_INCLUSIVE_CHANNELS = frozenset({CHANNEL_KEETA, CHANNEL_NOON})

#: Statement-level categories that ALSO appear, partially, on the channel's
#: orders — the order column each is carried in. Booked here as a true-up.
_ALSO_ON_ORDERS = {
    CHANNEL_NOON: {
        "payment_fee": Order.payment_fee,
        "cancellation_fee": Order.cancellation_fee,
    },
}

#: Statement-table channel code → the P&L's channel code (`order_pnl.CHANNELS`).
_PNL_CHANNEL = {CHANNEL_NOON: "noon_food"}


def pnl_channel_for(statement_channel: str) -> str:
    return _PNL_CHANNEL.get(statement_channel, statement_channel)


@dataclass
class PeriodCharge:
    """One category of non-order charges on one channel over the window."""

    channel: str
    category: str
    description: str | None
    #: As billed, VAT included; positive is a cost, negative a credit.
    amount: Decimal
    #: The VAT inside `amount` — reclaimable input VAT.
    input_vat: Decimal
    first_date: str
    last_date: str
    lines: int
    #: This is the part of a statement fee the orders did not already carry.
    is_true_up: bool = False


async def _on_order_amounts(
    db: AsyncSession, channel: str, category: str, statement_ids: set[str]
) -> dict[str, Decimal]:
    """What the statements' own orders already carry of `category`, per statement."""
    column = _ALSO_ON_ORDERS[channel][category]
    rows = await db.execute(
        select(AggregatorOrder.statement_id, func.sum(func.coalesce(column, 0)))
        .join(Order, Order.id == AggregatorOrder.mm_order_id)
        .where(
            AggregatorOrder.channel == channel,
            AggregatorOrder.statement_id.in_(statement_ids),
            in_pnl_clause(),
        )
        .group_by(AggregatorOrder.statement_id)
    )
    return {sid: to_decimal(total) for sid, total in rows}


async def period_charges(
    db: AsyncSession,
    date_from: str,
    date_to: str,
    channels: set[str] | None = None,
) -> list[PeriodCharge]:
    """
    Non-order marketplace charges whose statement line falls in
    [`date_from`, `date_to`], optionally narrowed to P&L `channels`.
    """
    ln = AggregatorStatementLine
    stmt = select(
        ln.channel,
        ln.statement_id,
        ln.line_type,
        ln.fee_category,
        ln.description,
        ln.line_date,
        ln.amount,
        cats.is_vat(ln).label("is_vat"),
    ).where(
        or_(ln.external_order_id.is_(None), ln.external_order_id == ""),
        ln.line_date >= date_from,
        ln.line_date <= date_to,
        # The gross / payout plumbing is not a charge.
        ~cats.is_gross(ln),
        ~cats.is_net(ln),
    )
    rows = (await db.execute(stmt)).all()
    if channels is not None:
        rows = [r for r in rows if pnl_channel_for(r.channel) in channels]

    # (channel, category) → running totals, gross (as billed) and VAT itemised.
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "gross": Decimal(0),
            "vat": Decimal(0),
            "dates": [],
            "lines": 0,
            "description": None,
            "statements": defaultdict(Decimal),
        }
    )

    def category_of(r) -> str:
        return (r.fee_category or r.line_type or "other").lower()

    # An itemised VAT line (Deliveroo's monthly fee) carries the same statement
    # description as the charge it taxes, which is how it finds its charge.
    charge_for_description = {
        (r.channel, r.description): category_of(r) for r in rows if not r.is_vat
    }
    for r in rows:
        cost = -to_decimal(r.amount)  # a fee is booked negative
        if r.is_vat:
            category = charge_for_description.get((r.channel, r.description), "vat")
            g = groups[(r.channel, category)]
            g["vat"] += cost
        else:
            category = category_of(r)
            g = groups[(r.channel, category)]
            g["gross"] += cost
            g["description"] = g["description"] or r.description
            if r.statement_id:
                g["statements"][r.statement_id] += cost
        g["dates"].append(r.line_date)
        g["lines"] += 1

    out: list[PeriodCharge] = []
    for (channel, category), g in sorted(groups.items()):
        gross = g["gross"]
        true_up = category in _ALSO_ON_ORDERS.get(channel, {})
        if true_up and g["statements"]:
            carried = await _on_order_amounts(
                db, channel, category, set(g["statements"])
            )
            gross -= sum(carried.values(), Decimal(0))
        if channel in _VAT_INCLUSIVE_CHANNELS and not g["vat"]:
            net = gross / (1 + VAT_RATE)
            vat = gross - net
        else:
            net, vat = gross, g["vat"]
        if money(net) == 0 and money(vat) == 0:
            continue
        out.append(
            PeriodCharge(
                channel=pnl_channel_for(channel),
                category=category,
                description=g["description"],
                amount=money(net + vat),
                input_vat=money(vat),
                first_date=min(g["dates"]),
                last_date=max(g["dates"]),
                lines=g["lines"],
                is_true_up=true_up,
            )
        )
    return out
