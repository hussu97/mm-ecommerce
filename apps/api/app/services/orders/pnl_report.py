"""
The profit & loss page: `order_pnl` summed per channel, plus period charges,
then misc purchase-order spend below PC3 (`misc_expenses`).

Kept apart from `order_pnl` because it is the one reader that adds something no
order carries — the marketplaces' non-order statement charges
(`aggregators.period_charges`) — and that module itself leans on `order_pnl` to
know which orders count.

The date window is the dashboard's and the orders list's: an order belongs to
the shop day it was **created** in (`business_day_service.range_bounds`), so a
P&L figure clicked through to the orders list lands on the same orders. A period
charge belongs to the day its statement line is dated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.order import Order
from app.models.pos_order import OrderSourceEnum
from app.services.aggregators.period_charges import PeriodCharge, period_charges
from app.services.orders import tax_identity_service
from app.services.orders.misc_expenses import MiscExpense, misc_expenses
from app.services.orders.order_pnl import (
    CHANNELS,
    PnlTotals,
    channel_expression,
    totals_by_channel,
)
from app.services.pos import business_day_service

__all__ = ["PnlReport", "build"]


@dataclass
class PnlReport:
    date_from: str
    date_to: str
    channels: list[tuple[str, PnlTotals]]
    total: PnlTotals
    period_charges: list[PeriodCharge]
    period_charges_included: bool
    #: Misc PO spend per category, prorated into the window. Overhead with no
    #: channel, so it sits below PC3 on the total only.
    misc_expenses: list[MiscExpense]
    #: False under a channel filter — overhead belongs to no channel.
    misc_expenses_included: bool

    @property
    def misc_expenses_total(self) -> Decimal:
        return money(sum((row.amount for row in self.misc_expenses), Decimal("0")))

    @property
    def pc4(self) -> Decimal:
        """PC3 less the misc spend: what the period made after overheads."""
        return money(self.total.pc3 - self.misc_expenses_total)


async def build(
    db: AsyncSession,
    *,
    date_from: str,
    date_to: str,
    channels: list[str] | None = None,
    branch_ids: list[uuid.UUID] | None = None,
    legal_entity_ids: list[uuid.UUID] | None = None,
    include_gated_misc: bool = False,
) -> PnlReport:
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    start, end = await business_day_service.range_bounds(db, date_from, date_to)
    where = [Order.created_at >= start, Order.created_at <= end]
    if channels:
        where.append(channel_expression().in_(channels))
    if branch_ids:
        where.append(Order.branch_id.in_(branch_ids))
    if legal_entity_ids:
        where.append(Order.legal_entity_id.in_(legal_entity_ids))

    by_channel = await totals_by_channel(db, *where)

    # Period charges are billed per marketplace account, not per kitchen, so a
    # branch slice leaves them out rather than guessing. They do belong to one
    # entity: the one marketplace orders are booked under (Fatema / Melting
    # Moments — the same fallback `tax_identity_service` gives any aggregator
    # order), so an entity slice keeps them only when it includes that entity.
    include_period = not branch_ids
    if include_period and legal_entity_ids:
        marketplace_entity = await tax_identity_service.resolve(
            db, branch_id=None, source=OrderSourceEnum.AGGREGATOR.value
        )
        include_period = (
            marketplace_entity is not None and marketplace_entity.id in legal_entity_ids
        )
    charges: list[PeriodCharge] = []
    if include_period:
        charges = await period_charges(
            db, date_from, date_to, set(channels) if channels else None
        )
    for charge in charges:
        column = by_channel.setdefault(charge.channel, PnlTotals())
        column.period_charges = money(column.period_charges + charge.amount)
        column.fees_vat = money(column.fees_vat + charge.input_vat)

    ordered = [
        (code, by_channel[code]) for code in (*CHANNELS, "other") if code in by_channel
    ]
    total = PnlTotals()
    for _, column in ordered:
        total.add(column)

    include_misc = not channels
    misc = (
        await misc_expenses(
            db,
            date_from,
            date_to,
            branch_ids=branch_ids,
            legal_entity_ids=legal_entity_ids,
            include_gated=include_gated_misc,
        )
        if include_misc
        else []
    )
    return PnlReport(
        date_from=date_from,
        date_to=date_to,
        channels=ordered,
        total=total,
        period_charges=charges,
        period_charges_included=include_period,
        misc_expenses=misc,
        misc_expenses_included=include_misc,
    )
