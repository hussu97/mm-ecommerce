"""
Misc purchase-order spend on the P&L: below PC3, one line per category.

A misc PO line (rent, groceries, a trade licence) is overhead, not the cost of
any order, so it has no channel and sits under PC3 on the total only. It is
spread **equally per day** over the line's own ``period_from``–``period_to``,
and the report counts only the days that fall inside its window: a year's rent
of 12,000 shows 986.30 in a 30-day month (12,000 × 30 / 365).

Which lines count: those on a **received** PO (``partially_received`` or
``closed`` — the set the VAT reclaim reads), so a planned or voided order is
never a cost. The branch filter is the PO's branch; the entity filter resolves
that branch's counter entity, as the VAT ledger does for purchases.

The amount is **net of VAT when the entity reclaims it** — the P&L's rule for
every cost (reclaimed input VAT is not a cost) — and the full gross when it
cannot (the Barsha counter's entity is not VAT-registered). The VAT split
itself is the supplier's, frozen on the line (``supplier_service``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.inventory import (
    PurchaseOrder,
    PurchaseOrderMiscCategory,
    PurchaseOrderMiscItem,
    PurchaseOrderStatusEnum,
)
from app.services.orders import tax_identity_service

__all__ = ["MiscExpense", "RECEIVED_STATUSES", "misc_expenses", "prorate"]

#: A PO is spend once its delivery is in (the VAT reclaim's rule too).
RECEIVED_STATUSES = (
    PurchaseOrderStatusEnum.PARTIALLY_RECEIVED.value,
    PurchaseOrderStatusEnum.CLOSED.value,
)


@dataclass
class MiscExpense:
    category_id: uuid.UUID
    category: str
    admin_only: bool
    #: The window's share of the lines' cost, quantised once at the end.
    amount: Decimal
    #: Distinct misc lines contributing.
    lines: int


def prorate(
    amount: Decimal,
    period_from: date,
    period_to: date,
    window_from: date,
    window_to: date,
) -> Decimal:
    """``amount`` spread equally over ``period_from..period_to`` (inclusive),
    keeping the days inside ``window_from..window_to``. Unrounded — the SQL
    below is the same formula; this is its readable twin for tests."""
    days = (period_to - period_from).days + 1
    overlap = (min(period_to, window_to) - max(period_from, window_from)).days + 1
    if days <= 0 or overlap <= 0:
        return Decimal("0")
    return Decimal(str(amount)) * overlap / days


async def misc_expenses(
    db: AsyncSession,
    date_from: str,
    date_to: str,
    *,
    branch_ids: list[uuid.UUID] | None = None,
    legal_entity_ids: list[uuid.UUID] | None = None,
    include_gated: bool = False,
) -> list[MiscExpense]:
    """Each category's misc spend in ``[date_from, date_to]``, alphabetical.

    ``include_gated`` keeps admin-only categories (rent, salary); it is the
    viewer's ``po_misc_service.can_see_gated``.
    """
    window_from = date.fromisoformat(date_from)
    window_to = date.fromisoformat(date_to)
    lo = literal(window_from, Date)
    hi = literal(window_to, Date)
    line = PurchaseOrderMiscItem
    days = line.period_to - line.period_from + 1
    overlap = func.least(line.period_to, hi) - func.greatest(line.period_from, lo) + 1
    share = cast(overlap, Numeric) / cast(days, Numeric)

    stmt = (
        select(
            PurchaseOrderMiscCategory.id,
            PurchaseOrderMiscCategory.name,
            PurchaseOrderMiscCategory.admin_only,
            PurchaseOrder.branch_id,
            func.sum(line.net_total * share),
            func.sum(line.entered_total * share),
            func.count(line.id),
        )
        .select_from(line)
        .join(PurchaseOrder, PurchaseOrder.id == line.purchase_order_id)
        .join(
            PurchaseOrderMiscCategory,
            PurchaseOrderMiscCategory.id == line.category_id,
        )
        .where(
            PurchaseOrder.status.in_(RECEIVED_STATUSES),
            line.period_from <= hi,
            line.period_to >= lo,
        )
        .group_by(
            PurchaseOrderMiscCategory.id,
            PurchaseOrderMiscCategory.name,
            PurchaseOrderMiscCategory.admin_only,
            PurchaseOrder.branch_id,
        )
    )
    if branch_ids:
        stmt = stmt.where(PurchaseOrder.branch_id.in_(branch_ids))
    if not include_gated:
        stmt = stmt.where(PurchaseOrderMiscCategory.admin_only.is_(False))

    totals: dict[uuid.UUID, MiscExpense] = {}
    raw: dict[uuid.UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    for category_id, name, admin_only, branch_id, net, gross, count in (
        await db.execute(stmt)
    ).all():
        entity = await tax_identity_service.resolve(
            db, branch_id=branch_id, source="cashier"
        )
        if legal_entity_ids and (entity is None or entity.id not in legal_entity_ids):
            continue
        reclaims = tax_identity_service.is_vat_registered(entity)
        raw[category_id] += Decimal(str((net if reclaims else gross) or 0))
        row = totals.setdefault(
            category_id,
            MiscExpense(
                category_id=category_id,
                category=name,
                admin_only=bool(admin_only),
                amount=Decimal("0"),
                lines=0,
            ),
        )
        row.lines += int(count)
    for category_id, row in totals.items():
        row.amount = money(raw[category_id])
    return sorted(
        (row for row in totals.values() if row.amount != 0 or row.lines),
        key=lambda row: row.category.lower(),
    )
