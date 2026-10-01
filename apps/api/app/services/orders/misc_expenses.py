"""
Misc purchase-order spend on the P&L: placed at a level, split across channels.

A misc PO line (rent, salaries, cake supplies, a trade licence) is not the cost
of any one order, so it is never on an order's own P&L. The report places it:

**When.** Spread **equally per day** over the line's own ``period_from``–
``period_to``, counting only the days inside the report's window: a year's rent
of 12,000 shows 986.30 in a 30-day month (12,000 × 30 / 365).

**At which level.** PC1 (a cost of the goods — cake supplies for custom
orders), PC2, PC3, or PC4 (overhead below PC3, the default).

**On which sales.** A set of channels and a set of branches; empty means all.
The line's cost is split over the *(channel, branch, entity)* cells of the
window's sales inside that set **in proportion to their GMV**, so the line
reads as the same % of GMV in every column it lands in, even though the AED
differ. A cost with no placement at all — salaries — is spread over everything.

Where the placement comes from, field by field (`resolve_placement`): the PO's
**supplier** (``Supplier.misc_pnl_*`` — the only place a branch is set, since
every misc line is filed on whichever branch's PO it rode on, which says where
it was bought, not whom it is for), else its **category**
(``PurchaseOrderMiscCategory.pnl_*``), else the default (PC4, everything).

**No sales to carry it.** When the targeted cells sold nothing in the window: a
line placed on named channels is split **equally** over those channels (and its
named branches), so a channel-specific cost still shows against its channel; a
line with no channel placement is **unallocated** — the total column carries it,
no channel does, and a filtered view leaves it out (it belongs to no slice).

**The total never moves.** Placement only redistributes. Each category's total
is quantised once from the unrounded sum, then shared out across its cells by
largest remainder, so its cells add up to exactly the figure the category would
show unsplit — the same PC4 as before any of this was configured.

Which lines count: those on a **received** PO (``partially_received`` or
``closed`` — the set the VAT reclaim reads), so a planned or voided order is
never a cost.

The amount is the **gross, VAT included** — what the invoice says, so a row
matches the PO a person can open. The input VAT the buying entity reclaims on it
(the PO branch's counter entity, as the VAT ledger books purchases) is its own
figure, ``vat``, credited back on a line at the same level; net of that credit
each level carries exactly the net cost. Zero where the entity cannot reclaim
(the Barsha counter's entity is not VAT-registered).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import Date, Numeric, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import CENTS, money
from app.models.inventory import (
    MISC_PNL_LEVELS,
    PurchaseOrder,
    PurchaseOrderMiscCategory,
    PurchaseOrderMiscItem,
    PurchaseOrderStatusEnum,
    Supplier,
)
from app.services.orders import tax_identity_service

__all__ = [
    "Cell",
    "DEFAULT_LEVEL",
    "MiscAllocation",
    "MiscLine",
    "Placement",
    "RECEIVED_STATUSES",
    "allocate",
    "misc_lines",
    "prorate",
    "resolve_placement",
]

#: A PO is spend once its delivery is in (the VAT reclaim's rule too).
RECEIVED_STATUSES = (
    PurchaseOrderStatusEnum.PARTIALLY_RECEIVED.value,
    PurchaseOrderStatusEnum.CLOSED.value,
)

#: Overhead below PC3, where every misc line sat before placement existed.
DEFAULT_LEVEL = "pc4"

#: The grain a cost is split to: (P&L channel, branch, legal entity) of the
#: sales that carry it. Branch and entity are None on an equal split over named
#: channels with no sales (there is no order to say which), so a branch or
#: entity filter leaves that part out.
Cell = tuple[str, uuid.UUID | None, uuid.UUID | None]


@dataclass(frozen=True)
class Placement:
    """Where a misc line lands: a level and the sales that carry it."""

    level: str = DEFAULT_LEVEL
    #: P&L channel codes; empty = every channel.
    channels: tuple[str, ...] = ()
    #: Empty = every branch.
    branch_ids: tuple[uuid.UUID, ...] = ()

    def covers(self, cell: Cell) -> bool:
        channel, branch_id, _ = cell
        return (not self.channels or channel in self.channels) and (
            not self.branch_ids or branch_id in self.branch_ids
        )


def resolve_placement(
    *,
    category_level: str | None,
    category_channels: Iterable[str] | None,
    supplier_level: str | None,
    supplier_channels: Iterable[str] | None,
    supplier_branch_ids: Iterable[uuid.UUID] | None,
) -> Placement:
    """Each field on its own: the supplier's if set, else the category's, else
    the default (PC4 / all channels). Branches are set on suppliers only."""
    channels = tuple(supplier_channels or ()) or tuple(category_channels or ())
    return Placement(
        level=supplier_level or category_level or DEFAULT_LEVEL,
        channels=channels,
        branch_ids=tuple(supplier_branch_ids or ()),
    )


@dataclass
class MiscLine:
    """One misc PO line's share of the window, unrounded."""

    category_id: uuid.UUID
    category: str
    admin_only: bool
    placement: Placement
    #: VAT included.
    gross: Decimal
    #: What it costs the buying entity: net when it reclaims the VAT, else gross.
    cost: Decimal


@dataclass
class MiscAllocation:
    """One category's spend at one level, split to cells — one P&L row."""

    category_id: uuid.UUID
    category: str
    admin_only: bool
    level: str
    #: Cell → (gross, VAT recovered), quantised; the cells of a category add up
    #: to its unsplit total exactly.
    cells: dict[Cell, tuple[Decimal, Decimal]] = field(default_factory=dict)
    #: The part no sales carried (see the module docstring), as (gross, VAT).
    unallocated: tuple[Decimal, Decimal] = (Decimal("0"), Decimal("0"))
    #: Distinct misc lines contributing.
    lines: int = 0

    def amounts(
        self, keep: Callable[[Cell], bool]
    ) -> dict[str, tuple[Decimal, Decimal]]:
        """(gross, VAT) per channel over the cells `keep` selects."""
        out: dict[str, tuple[Decimal, Decimal]] = {}
        for cell, (gross, vat) in self.cells.items():
            if keep(cell):
                g, v = out.get(cell[0], (Decimal("0"), Decimal("0")))
                out[cell[0]] = (g + gross, v + vat)
        return out


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


async def misc_lines(
    db: AsyncSession,
    date_from: str,
    date_to: str,
    *,
    include_gated: bool = False,
) -> list[MiscLine]:
    """Every received misc line overlapping ``[date_from, date_to]``, prorated
    into it and placed. ``include_gated`` keeps admin-only categories (rent,
    salary); it is the viewer's ``po_misc_service.can_see_gated``."""
    window_from = date.fromisoformat(date_from)
    window_to = date.fromisoformat(date_to)
    lo = literal(window_from, Date)
    hi = literal(window_to, Date)
    line = PurchaseOrderMiscItem
    category = PurchaseOrderMiscCategory
    days = line.period_to - line.period_from + 1
    overlap = func.least(line.period_to, hi) - func.greatest(line.period_from, lo) + 1
    share = cast(overlap, Numeric) / cast(days, Numeric)

    stmt = (
        select(
            category.id,
            category.name,
            category.admin_only,
            category.pnl_level,
            category.pnl_channels,
            Supplier.misc_pnl_level,
            Supplier.misc_pnl_channels,
            Supplier.misc_pnl_branch_ids,
            PurchaseOrder.branch_id,
            (line.net_total * share).label("net"),
            (line.entered_total * share).label("gross"),
        )
        .select_from(line)
        .join(PurchaseOrder, PurchaseOrder.id == line.purchase_order_id)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .join(category, category.id == line.category_id)
        .where(
            PurchaseOrder.status.in_(RECEIVED_STATUSES),
            line.period_from <= hi,
            line.period_to >= lo,
        )
    )
    if not include_gated:
        stmt = stmt.where(category.admin_only.is_(False))

    out: list[MiscLine] = []
    reclaims: dict[uuid.UUID | None, bool] = {}
    for row in (await db.execute(stmt)).all():
        if row.branch_id not in reclaims:
            entity = await tax_identity_service.resolve(
                db, branch_id=row.branch_id, source="cashier"
            )
            reclaims[row.branch_id] = tax_identity_service.is_vat_registered(entity)
        gross = Decimal(str(row.gross or 0))
        out.append(
            MiscLine(
                category_id=row.id,
                category=row.name,
                admin_only=bool(row.admin_only),
                placement=resolve_placement(
                    category_level=row.pnl_level,
                    category_channels=row.pnl_channels,
                    supplier_level=row.misc_pnl_level,
                    supplier_channels=row.misc_pnl_channels,
                    supplier_branch_ids=row.misc_pnl_branch_ids,
                ),
                gross=gross,
                cost=Decimal(str(row.net or 0)) if reclaims[row.branch_id] else gross,
            )
        )
    return out


#: The key a category's spend is shared out over: (level, cell), or
#: (level, None) for its unallocated part.
_Key = tuple[str, Cell | None]


def _split(amount: Decimal, line: MiscLine, gmv: Mapping[Cell, Decimal]):
    """`amount` of `line` per cell — unrounded; a None cell is unallocated."""
    placement = line.placement
    targets = {c: w for c, w in gmv.items() if w > 0 and placement.covers(c)}
    weight = sum(targets.values(), Decimal("0"))
    if weight > 0:
        return {c: amount * w / weight for c, w in targets.items()}
    if placement.channels:
        # Named channels that sold nothing still carry their cost, evenly.
        cells = [
            (channel, branch_id, None)
            for channel in placement.channels
            for branch_id in (placement.branch_ids or (None,))
        ]
        return {c: amount / len(cells) for c in cells}
    return {None: amount}


def _largest_remainder(raw: Mapping[_Key, Decimal], keys: list[_Key]) -> dict:
    """Quantise ``raw`` to the fils so the parts sum to exactly
    ``money(sum(raw))``: floor each, then hand the leftover fils to the largest
    remainders (ties in ``keys`` order, so the result is deterministic)."""
    total = money(sum(raw.values(), Decimal("0")))
    floors = {k: raw[k].quantize(CENTS, rounding=ROUND_FLOOR) for k in keys}
    leftover = int((total - sum(floors.values(), Decimal("0"))) / CENTS)
    by_remainder = sorted(keys, key=lambda k: raw[k] - floors[k], reverse=True)
    for k in by_remainder[:leftover]:
        floors[k] += CENTS
    return floors


def allocate(
    lines: Iterable[MiscLine], gmv: Mapping[Cell, Decimal]
) -> list[MiscAllocation]:
    """Split each line over the cells its placement covers, by ``gmv`` (the
    window's GMV per cell, unfiltered), and roll them up per category and
    level. Ordered by level, then category name."""
    by_category: dict[uuid.UUID, list[MiscLine]] = defaultdict(list)
    for line in lines:
        by_category[line.category_id].append(line)

    rows: list[MiscAllocation] = []
    for category_lines in by_category.values():
        gross: dict[_Key, Decimal] = defaultdict(lambda: Decimal("0"))
        cost: dict[_Key, Decimal] = defaultdict(lambda: Decimal("0"))
        count: dict[str, int] = defaultdict(int)
        for line in category_lines:
            level = line.placement.level
            count[level] += 1
            for cell, part in _split(line.gross, line, gmv).items():
                gross[(level, cell)] += part
            for cell, part in _split(line.cost, line, gmv).items():
                cost[(level, cell)] += part
        keys = sorted(gross.keys() | cost.keys(), key=_sort_key)
        for k in keys:
            gross.setdefault(k, Decimal("0"))
            cost.setdefault(k, Decimal("0"))
        q_gross = _largest_remainder(gross, keys)
        q_cost = _largest_remainder(cost, keys)

        first = category_lines[0]
        per_level: dict[str, MiscAllocation] = {}
        for level, cell in keys:
            row = per_level.setdefault(
                level,
                MiscAllocation(
                    category_id=first.category_id,
                    category=first.category,
                    admin_only=first.admin_only,
                    level=level,
                    lines=count[level],
                ),
            )
            g = q_gross[(level, cell)]
            # VAT is the difference of the two quantised figures rather than a
            # third rounding, so gross − VAT recovered is the net cost to the fils.
            v = g - q_cost[(level, cell)]
            if cell is None:
                row.unallocated = (g, v)
            elif g or v:
                row.cells[cell] = (g, v)
        rows.extend(per_level.values())
    return sorted(
        rows,
        key=lambda r: (MISC_PNL_LEVELS.index(r.level), r.category.lower()),
    )


def _sort_key(key: _Key):
    level, cell = key
    if cell is None:
        return (level, 1, "", "", "")
    channel, branch_id, entity_id = cell
    return (level, 0, channel, str(branch_id or ""), str(entity_id or ""))
