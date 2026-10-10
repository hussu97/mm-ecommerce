"""
The profit & loss page: `order_pnl` summed per channel, plus period charges,
plus misc purchase-order spend placed at its level (`misc_expenses`).

Kept apart from `order_pnl` because it is the one reader that adds something no
order carries — the marketplaces' non-order statement charges
(`aggregators.period_charges`) and the misc PO spend — and that module itself
leans on `order_pnl` to know which orders count.

The date window is the dashboard's and the orders list's: an order belongs to
the shop day it was **created** in (`business_day_service.range_bounds`), so a
P&L figure clicked through to the orders list lands on the same orders. A period
charge belongs to the day its statement line is dated.

**One read, then filters.** The window's orders are summed once per (channel,
branch, legal entity) cell, unfiltered, and the channel / branch / entity
filters pick cells. The unfiltered cells are what misc spend is split over by
GMV — a cost's share of a column must not depend on what else is filtered out
— so a column shows the same figures whatever its neighbours are.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.order import Order
from app.models.pos_order import OrderSourceEnum
from app.services.aggregators.period_charges import (
    PeriodCharge,
    period_charges,
    period_refunds,
)
from app.services.orders import tax_identity_service
from app.services.orders.misc_expenses import (
    Cell,
    MiscAllocation,
    allocate,
    misc_lines,
)
from app.services.orders.order_pnl import CHANNELS, PnlTotals, totals_by_cell
from app.services.pos import business_day_service

__all__ = ["PnlReport", "build"]


@dataclass
class PnlReport:
    date_from: str
    date_to: str
    #: Each column carries its misc spend at every level (`misc_pc1`…), so
    #: its PC1–PC4 are final.
    channels: list[tuple[str, PnlTotals]]
    #: The channels summed, plus the unallocated misc spend (unfiltered only).
    total: PnlTotals
    period_charges: list[PeriodCharge]
    period_charges_included: bool
    #: Misc PO spend per category and level, split to cells. Read through
    #: `misc_amounts` for the figures under this report's filters.
    misc: list[MiscAllocation]
    #: The filters this report was built under, as a cell predicate.
    keep: Callable[[Cell], bool]
    #: True when no channel, branch or entity filter is set — the only view in
    #: which the unallocated misc spend (no sales carried it) is shown.
    unfiltered: bool

    def misc_amounts(self, row: MiscAllocation) -> dict[str, tuple[Decimal, Decimal]]:
        """(gross, VAT recovered) of `row` per channel code under the filters,
        and under `total`."""
        out = row.amounts(self.keep)
        gross = sum((g for g, _ in out.values()), Decimal("0"))
        vat = sum((v for _, v in out.values()), Decimal("0"))
        if self.unfiltered:
            gross += row.unallocated[0]
            vat += row.unallocated[1]
        out["total"] = (gross, vat)
        return out

    @property
    def misc_unallocated(self) -> Decimal:
        """Misc spend no sales carried. In the total when unfiltered; left out
        of a filtered view, which it belongs to no slice of."""
        return money(sum((r.unallocated[0] for r in self.misc), Decimal("0")))


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
    # `reporting_at`: a custom order counts on the day it was handed over.
    cells = await totals_by_cell(
        db, Order.reporting_at >= start, Order.reporting_at <= end
    )

    channel_set = set(channels or ())
    branch_set = set(branch_ids or ())
    entity_set = set(legal_entity_ids or ())

    def keep(cell: Cell) -> bool:
        channel, branch_id, entity_id = cell
        return (
            (not channel_set or channel in channel_set)
            and (not branch_set or branch_id in branch_set)
            and (not entity_set or entity_id in entity_set)
        )

    by_channel: dict[str, PnlTotals] = {}
    for cell, totals in cells.items():
        if keep(cell):
            by_channel.setdefault(cell[0], PnlTotals()).add(totals)

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
        charges = await period_charges(db, date_from, date_to, channel_set or None)
    for charge in charges:
        column = by_channel.setdefault(charge.channel, PnlTotals())
        column.period_charges = money(column.period_charges + charge.amount)
        column.fees_vat = money(column.fees_vat + charge.input_vat)
    # Refunds a marketplace charged with no order (Talabat's Order Compensation):
    # report-only like the charges above, on the same entity rule, and booked
    # with the refunds. No VAT comes back with them (see `period_refunds`).
    if include_period:
        for code, amount in (
            await period_refunds(db, date_from, date_to, channel_set or None)
        ).items():
            column = by_channel.setdefault(code, PnlTotals())
            column.refunds = money(column.refunds + amount)
            column.period_refunds = money(column.period_refunds + amount)

    # Split over every cell's GMV, unfiltered (see the module docstring).
    misc = allocate(
        await misc_lines(db, date_from, date_to, include_gated=include_gated_misc),
        {cell: totals.gmv for cell, totals in cells.items()},
    )
    for row in misc:
        for code, (gross, vat) in row.amounts(keep).items():
            column = by_channel.setdefault(code, PnlTotals())
            _add_misc(column, row.level, gross, vat)

    ordered = [
        (code, by_channel[code]) for code in (*CHANNELS, "other") if code in by_channel
    ]
    total = PnlTotals()
    for _, column in ordered:
        total.add(column)
    unfiltered = not (channel_set or branch_set or entity_set)
    if unfiltered:
        for row in misc:
            _add_misc(total, row.level, *row.unallocated)

    return PnlReport(
        date_from=date_from,
        date_to=date_to,
        channels=ordered,
        total=total,
        period_charges=charges,
        period_charges_included=include_period,
        misc=misc,
        keep=keep,
        unfiltered=unfiltered,
    )


def _add_misc(column: PnlTotals, level: str, gross: Decimal, vat: Decimal) -> None:
    key = f"misc_{level}"
    setattr(column, key, money(getattr(column, key) + gross))
    setattr(column, f"{key}_vat", money(getattr(column, f"{key}_vat") + vat))
