"""
The profit & loss page: GMV → PC1 → PC2 → PC3 per channel, net of VAT.

A thin route over `services/orders/pnl_report`, which owns the arithmetic (via
`order_pnl`, the same expressions the orders list and each order's breakdown
read). Gated on `reports.cost`: it is the shop's margins by channel, the same
audience as the cost-analysis reports.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.permissions import require
from app.models import User
from app.schemas.pnl import (
    PnlChannelColumn,
    PnlMiscAmount,
    PnlMiscExpenseRow,
    PnlMiscExpenses,
    PnlPeriodChargeRow,
    PnlPurchaseOrderRow,
    PnlPurchaseOrdersResponse,
    PnlReportResponse,
    PnlVatSummary,
)
from app.services.inventory import po_misc_service
from app.services.orders import order_pnl, pnl_purchase_orders, pnl_report

router = APIRouter()

_DATE = r"^\d{4}-\d{2}-\d{2}$"


def _column(code: str, totals: order_pnl.PnlTotals) -> PnlChannelColumn:
    return PnlChannelColumn(
        channel=code,
        orders=totals.orders,
        charged_cancellations=totals.charged_cancellations,
        orders_with_cogs=totals.orders_with_cogs,
        orders_fees_pending=totals.orders_fees_pending,
        cogs_provisional=float(totals.cogs_provisional),
        **order_pnl.statement_payload(totals),
    )


@router.get("", response_model=PnlReportResponse)
async def profit_and_loss(
    date_from: str = Query(..., pattern=_DATE, description="Inclusive shop day"),
    date_to: str = Query(..., pattern=_DATE, description="Inclusive shop day"),
    channels: list[str] | None = Query(
        None,
        description="P&L channel codes (multi): `counter`, `website_delivery`, "
        "`website_pickup`, `custom`, `talabat`, `keeta`, `noon_food`, `deliveroo`, "
        "`careem`",
    ),
    branch_ids: list[uuid.UUID] | None = Query(None),
    legal_entity_ids: list[uuid.UUID] | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.cost")),
) -> PnlReportResponse:
    unknown = set(channels or []) - set(order_pnl.CHANNELS)
    if unknown:
        raise BadRequestError(f"Unknown channel(s): {', '.join(sorted(unknown))}")
    report = await pnl_report.build(
        db,
        date_from=date_from,
        date_to=date_to,
        channels=channels,
        branch_ids=branch_ids,
        legal_entity_ids=legal_entity_ids,
        include_gated_misc=po_misc_service.can_see_gated(_admin),
    )
    total = report.total
    return PnlReportResponse(
        date_from=report.date_from,
        date_to=report.date_to,
        channels=[_column(code, col) for code, col in report.channels],
        total=_column("total", total),
        period_charges=[
            PnlPeriodChargeRow(
                channel=c.channel,
                category=c.category,
                description=c.description,
                amount=float(c.amount),
                input_vat=float(c.input_vat),
                first_date=c.first_date,
                last_date=c.last_date,
                lines=c.lines,
            )
            for c in report.period_charges
        ],
        period_charges_included=report.period_charges_included,
        vat=PnlVatSummary(
            output_vat=float(total.output_vat),
            fees_vat_reclaimed=float(total.fees_vat),
            net_vat=float(total.net_vat),
        ),
        misc_expenses=PnlMiscExpenses(
            rows=[_misc_row(report, row) for row in report.misc],
            unallocated=float(report.misc_unallocated),
            unallocated_included=report.unfiltered,
        ),
    )


@router.get("/purchase-orders", response_model=PnlPurchaseOrdersResponse)
async def purchase_order_stock(
    date_from: str = Query(..., pattern=_DATE, description="Inclusive shop day"),
    date_to: str = Query(..., pattern=_DATE, description="Inclusive shop day"),
    branch_ids: list[uuid.UUID] | None = Query(None),
    legal_entity_ids: list[uuid.UUID] | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.cost")),
) -> PnlPurchaseOrdersResponse:
    """The window's closed POs' stock, less what the P&L's orders have drawn of
    it to date. Its own request, not part of `/profit-loss`: it traces stock
    through every batch and transfer, and the statement should not wait on it."""
    report = await pnl_purchase_orders.build(
        db,
        date_from=date_from,
        date_to=date_to,
        branch_ids=branch_ids,
        legal_entity_ids=legal_entity_ids,
    )
    return PnlPurchaseOrdersResponse(
        date_from=date_from,
        date_to=date_to,
        received=float(report.received),
        consumed=float(report.consumed),
        net=float(report.net),
        used_elsewhere=float(report.used_elsewhere),
        in_stock=float(report.in_stock),
        purchase_orders=[
            PnlPurchaseOrderRow(
                purchase_order_id=row.purchase_order_id,
                reference=row.reference,
                business_date=row.business_date,
                supplier=row.supplier,
                branch=row.branch,
                received=float(row.received),
                consumed=float(row.consumed),
                net=float(row.net),
                used_elsewhere=float(row.used_elsewhere),
                in_stock=float(row.in_stock),
            )
            for row in _biggest_supplier_first(report.rows)
        ],
    )


def _biggest_supplier_first(rows: list) -> list:
    """The supplier paid most in the window first, its POs together under it,
    each biggest first; the newest breaks a tie."""
    by_supplier: dict[str, Decimal] = {}
    for row in rows:
        by_supplier[row.supplier] = by_supplier.get(row.supplier, Decimal(0)) + (
            row.received
        )
    newest_first = sorted(
        rows, key=lambda r: (r.business_date, r.reference), reverse=True
    )
    return sorted(
        newest_first,
        key=lambda r: (-by_supplier[r.supplier], r.supplier.lower(), -r.received),
    )


def _misc_row(report: pnl_report.PnlReport, row) -> PnlMiscExpenseRow:
    columns = dict(report.channels)
    columns["total"] = report.total
    return PnlMiscExpenseRow(
        category_id=row.category_id,
        category=row.category,
        admin_only=row.admin_only,
        level=row.level,
        columns={
            code: PnlMiscAmount(
                amount=float(gross),
                share=_float(columns[code].share(gross)) if code in columns else None,
                vat_recovered=float(vat),
            )
            for code, (gross, vat) in report.misc_amounts(row).items()
        },
        lines=row.lines,
    )


def _float(value) -> float | None:
    return None if value is None else float(value)
