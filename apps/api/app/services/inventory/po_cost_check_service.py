"""Is this purchase a good price? A draft PO line against what the item cost before.

The PO form asks it for every inventory line while the buyer types: the line's
unit cost as the PO would store it, the item's latest received purchase, and its
current weighted average cost, with a better / same / worse verdict against
each. The arithmetic lives here rather than in the form (CLAUDE.md §10) — the
unit cost is ``split_line_vat``'s, the same figure the saved line will carry.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Date, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import rate as _rate
from app.core.money import unit_cost as _c
from app.models.branch import Branch
from app.models.inventory import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
)
from app.schemas.inventory import (
    PurchaseLineCostCheck,
    PurchaseLineCostCheckLine,
    PurchaseLineCostComparison,
    PurchaseLineLastPurchase,
)
from app.services.inventory import cost_layer_service, supplier_service

__all__ = ["SAME_WITHIN", "check_lines", "compare", "latest_purchases"]

#: Within half a percent either way reads as "same". Prices are keyed as line
#: totals, so a strict comparison would call 10.004 against 10.00 "worse" — the
#: percentage is always shown beside the verdict for anyone who wants the fils.
SAME_WITHIN = Decimal("0.005")


def compare(unit_cost: Decimal, reference: Decimal) -> PurchaseLineCostComparison:
    """``unit_cost`` against ``reference``: paying less is better."""
    difference = _c(unit_cost - reference)
    if reference > 0:
        fraction = difference / reference
        change: Decimal | None = _rate(fraction)
        if abs(fraction) <= SAME_WITHIN:
            verdict = "same"
        else:
            verdict = "worse" if difference > 0 else "better"
    else:
        # Free last time: any price is worse, the same zero is the same.
        change = None
        verdict = "same" if difference == 0 else "worse"
    return PurchaseLineCostComparison(
        reference_cost=_c(reference),
        difference=difference,
        change=change,
        verdict=verdict,
    )


async def latest_purchases(
    db: AsyncSession,
    item_ids: list[uuid.UUID],
    *,
    branch_scope: Select | None,
) -> dict[uuid.UUID, PurchaseLineLastPurchase]:
    """Each item's most recent purchase that actually arrived.

    "Arrived" is a line with stock received on a PO that was not voided — a
    draft, a pending approval or a declined order was never paid for, and a
    voided one has had its stock reversed. Newest by invoice date (the day it
    was bought), falling back to the PO's business date, then by creation.

    Only storage-unit lines: the draft line is always priced per storage unit,
    and an ingredient-unit line's cost is per ingredient unit, so comparing the
    two would be off by the conversion factor.

    ``branch_scope`` limits the search to POs the caller can open (a subquery
    of branch ids); ``None`` searches every branch.
    """
    if not item_ids:
        return {}
    purchased_on = func.coalesce(
        PurchaseOrder.invoice_date, cast(PurchaseOrder.business_date, Date)
    )
    stmt = (
        select(
            PurchaseOrderItem.item_id,
            PurchaseOrderItem.quantity,
            PurchaseOrderItem.unit_cost,
            PurchaseOrder.id,
            PurchaseOrder.reference,
            purchased_on.label("purchased_on"),
            Supplier.name,
            Branch.name,
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .outerjoin(Branch, Branch.id == PurchaseOrder.branch_id)
        .where(
            PurchaseOrderItem.item_id.in_(item_ids),
            PurchaseOrderItem.unit == "storage",
            PurchaseOrderItem.received_quantity > 0,
            PurchaseOrder.status != PurchaseOrderStatusEnum.VOIDED.value,
        )
        .distinct(PurchaseOrderItem.item_id)
        .order_by(
            PurchaseOrderItem.item_id,
            purchased_on.desc().nulls_last(),
            PurchaseOrder.created_at.desc(),
        )
    )
    if branch_scope is not None:
        stmt = stmt.where(PurchaseOrder.branch_id.in_(branch_scope))
    out: dict[uuid.UUID, PurchaseLineLastPurchase] = {}
    for (
        item_id,
        quantity,
        line_cost,
        po_id,
        reference,
        on,
        supplier_name,
        branch_name,
    ) in (await db.execute(stmt)).all():
        out[item_id] = PurchaseLineLastPurchase(
            purchase_order_id=po_id,
            reference=reference,
            supplier_name=supplier_name,
            branch_name=branch_name,
            purchased_on=on,
            quantity=Decimal(str(quantity)),
            unit_cost=_c(line_cost),
        )
    return out


async def check_lines(
    db: AsyncSession,
    lines: list[PurchaseLineCostCheckLine],
    *,
    branch_scope: Select | None,
) -> list[PurchaseLineCostCheck]:
    """One answer per line, in the order asked."""
    item_ids = list({line.item_id for line in lines})
    last = await latest_purchases(db, item_ids, branch_scope=branch_scope)
    # Items with no costed stock are absent here — read as unknown, not zero.
    averages = await cost_layer_service.item_average_costs(db, item_ids)

    out: list[PurchaseLineCostCheck] = []
    for line in lines:
        unit_cost = None
        if line.quantity > 0 and line.entered_total > 0:
            # The VAT flag does not move the unit cost — it is gross either way.
            unit_cost = supplier_service.split_line_vat(
                line.entered_total, line.quantity, is_vat_deductible=False
            ).unit_cost
        previous = last.get(line.item_id)
        average = averages.get(line.item_id)
        out.append(
            PurchaseLineCostCheck(
                item_id=line.item_id,
                unit_cost=unit_cost,
                last_purchase=previous,
                vs_last_purchase=(
                    compare(unit_cost, previous.unit_cost)
                    if unit_cost is not None and previous is not None
                    else None
                ),
                average_cost=average,
                vs_average_cost=(
                    compare(unit_cost, average)
                    if unit_cost is not None and average is not None
                    else None
                ),
            )
        )
    return out
