"""
The profit & loss page: GMV → PC1 → PC2 → PC3 per channel, net of VAT.

A thin route over `services/orders/pnl_report`, which owns the arithmetic (via
`order_pnl`, the same expressions the orders list and each order's breakdown
read). Gated on `reports.cost`: it is the shop's margins by channel, the same
audience as the cost-analysis reports.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.permissions import require
from app.models import User
from app.schemas.pnl import (
    PnlChannelColumn,
    PnlPeriodChargeRow,
    PnlReportResponse,
    PnlVatSummary,
)
from app.services.orders import order_pnl, pnl_report

router = APIRouter()

_DATE = r"^\d{4}-\d{2}-\d{2}$"


def _floats(fields: dict) -> dict:
    return {k: (None if v is None else float(v)) for k, v in fields.items()}


def _column(code: str, totals: order_pnl.PnlTotals) -> PnlChannelColumn:
    return PnlChannelColumn(
        channel=code,
        orders=totals.orders,
        charged_cancellations=totals.charged_cancellations,
        orders_with_cogs=totals.orders_with_cogs,
        orders_fees_pending=totals.orders_fees_pending,
        cogs_provisional=float(totals.cogs_provisional),
        **_floats(order_pnl.statement_fields(totals)),
    )


@router.get("", response_model=PnlReportResponse)
async def profit_and_loss(
    date_from: str = Query(..., pattern=_DATE, description="Inclusive shop day"),
    date_to: str = Query(..., pattern=_DATE, description="Inclusive shop day"),
    channels: list[str] | None = Query(
        None,
        description="P&L channel codes (multi): `counter`, `website_delivery`, "
        "`website_pickup`, `talabat`, `keeta`, `noon_food`, `deliveroo`, `careem`",
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
    )
