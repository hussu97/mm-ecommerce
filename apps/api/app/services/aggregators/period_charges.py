"""The marketplace charges no order carries, for the P&L — dated by statement.

A marketplace bills some costs per period rather than per order: Deliveroo's
AED 200/outlet monthly platform fee (and its correction credits), noon's monthly
platform fee (149 + 5% VAT) and its monthly long-distance fee, a Keeta
subscription. They arrive as settlement lines with no `external_order_id`, so no
order's P&L can hold them, and they are booked here at the **statement line's date** — the date the
marketplace put the charge on its statement. That is the only date every
channel gives (a Deliveroo line's free-text "for the period July 1 to July 31"
is not parseable enough to trust), it is the date the shop can reconcile
against, and a monthly fee lands once a month either way.

Only charges that really have no order land here. noon's Tax Invoice also
lists a payment fee and a cancellation fee, but those are its per-order rows
summed. Every order carries its own share (`noon_provider._OVERVIEW_SUMMARY_FEES`
books only the platform and long-distance fees), so they are order costs, not
period charges.

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

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, to_decimal
from app.models.aggregator import (
    CHANNEL_KEETA,
    CHANNEL_NOON,
    AggregatorStatementLine,
)
from app.services.aggregators import statement_categories as cats
from app.services.orders.order_pricing import VAT_RATE

__all__ = ["PeriodCharge", "pnl_channel_for", "period_charges"]

#: Channels whose fee lines are stored VAT-inclusive with no separate VAT line.
_VAT_INCLUSIVE_CHANNELS = frozenset({CHANNEL_KEETA, CHANNEL_NOON})

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
        g["dates"].append(r.line_date)
        g["lines"] += 1

    out: list[PeriodCharge] = []
    for (channel, category), g in sorted(groups.items()):
        gross = g["gross"]
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
            )
        )
    return out
