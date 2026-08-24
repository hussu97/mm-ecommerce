"""POS reporting endpoints — sales, payments, tax, operations and inventory."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.permissions import require
from app.models.user import User
from app.schemas.reports import DailySalesEmailRequest, DailySalesEmailResponse
from app.services.pos import daily_sales_email, pos_reports

router = APIRouter()


class _Window:
    """Shared query parameters for every report."""

    def __init__(
        self,
        branch_id: uuid.UUID | None = None,
        date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        if date_from and date_to and date_from > date_to:
            raise BadRequestError("date_from must not be after date_to")
        self.branch_id = branch_id
        self.date_from = date_from
        self.date_to = date_to

    @property
    def kwargs(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "date_from": self.date_from,
            "date_to": self.date_to,
        }


@router.get("/sales/summary")
async def sales_summary(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.sales")),
):
    return await pos_reports.sales_summary(db, **window.kwargs)


@router.get("/sales/by")
async def sales_by(
    dimension: str,
    limit: int = Query(100, ge=1, le=1000),
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.sales")),
):
    """
    Sales grouped by any supported dimension.

    The allowed set comes from the service rather than a literal repeated
    here, so adding a dimension cannot leave the route rejecting it.
    """
    if dimension not in pos_reports.SUPPORTED_DIMENSIONS:
        raise BadRequestError(
            f"Unsupported dimension '{dimension}'. Try one of: "
            f"{', '.join(sorted(pos_reports.SUPPORTED_DIMENSIONS))}"
        )
    return await pos_reports.sales_by_dimension(
        db, dimension=dimension, limit=limit, **window.kwargs
    )


@router.get("/payments")
async def payments(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.sales")),
):
    return await pos_reports.payments_report(db, **window.kwargs)


@router.get("/taxes")
async def taxes(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.other")),
):
    """VAT return input: taxable base and tax collected per rate."""
    return await pos_reports.tax_report(db, **window.kwargs)


@router.get("/voids-returns")
async def voids_and_returns(
    limit: int = Query(200, ge=1, le=1000),
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.other")),
):
    return await pos_reports.voids_and_returns(db, limit=limit, **window.kwargs)


@router.get("/tills")
async def tills(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.other")),
):
    return await pos_reports.tills_report(db, **window.kwargs)


@router.get("/drawer-operations")
async def drawer_operations(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.other")),
):
    return await pos_reports.drawer_operations_report(db, **window.kwargs)


@router.get("/inventory/valuation")
async def inventory_valuation(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.inventory")),
):
    """Stock value on hand plus everything below its reorder point."""
    return await pos_reports.inventory_valuation(db, branch_id=branch_id)


@router.get("/inventory/cost-of-goods")
async def cost_of_goods(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.cost")),
):
    return await pos_reports.cost_of_goods(db, **window.kwargs)


@router.get("/menu-engineering")
async def menu_engineering(
    limit: int = Query(200, ge=1, le=1000),
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.cost")),
):
    """Star / plough-horse / puzzle / dog classification by volume and margin."""
    return await pos_reports.menu_engineering(db, limit=limit, **window.kwargs)


@router.get("/speed-of-service")
async def speed_of_service(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.other")),
):
    """Kitchen acknowledge, prep and total times over the window."""
    return await pos_reports.speed_of_service(db, **window.kwargs)


@router.get("/branches-trend")
async def branches_trend(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.sales")),
):
    """Sales per branch per business day."""
    return await pos_reports.branches_trend(db, **window.kwargs)


@router.get("/table-utilization")
async def table_utilization(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.other")),
):
    """Covers, turns, dwell time and sales per seat, for dine-in only."""
    return await pos_reports.table_utilization(db, **window.kwargs)


@router.get("/suppliers-analysis")
async def suppliers_analysis(
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.cost")),
):
    """Purchase-order count and spend per supplier."""
    return await pos_reports.suppliers_analysis(
        db, date_from=date_from, date_to=date_to
    )


@router.get("/cost-adjustment-history")
async def cost_adjustment_history(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.cost")),
):
    """Stock write-offs and revaluations, newest first."""
    return await pos_reports.cost_adjustment_history(db, **window.kwargs)


@router.get("/purchase-orders")
async def purchase_orders_report(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.cost")),
):
    """Purchase orders with ordered, received and outstanding value."""
    return await pos_reports.purchase_orders_report(db, **window.kwargs)


@router.get("/transfers")
async def transfers_report(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.cost")),
):
    """Stock moved between branches, both legs."""
    return await pos_reports.transfers_report(db, **window.kwargs)


@router.get("/sales-predictions")
async def sales_predictions(
    days_ahead: int = Query(7, ge=1, le=30),
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.sales")),
):
    """Forecast the coming days from each weekday's own history."""
    return await pos_reports.sales_predictions(
        db, branch_id=branch_id, days_ahead=days_ahead
    )


@router.post("/sales/daily-email", response_model=DailySalesEmailResponse)
async def send_daily_sales_email(
    body: DailySalesEmailRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("reports.sales")),
) -> DailySalesEmailResponse:
    """Build the daily sales spreadsheet for a window and email it now.

    The same report the nightly job sends, on demand: a console button picks a
    date range and a recipient list, and this returns per-recipient outcomes so
    the screen can say which addresses it reached. Gated on the sales-reports
    permission the figures themselves need.
    """
    result = await daily_sales_email.send(
        db,
        date_from=body.date_from,
        date_to=body.date_to,
        recipients=[str(r) for r in body.recipients],
    )
    return DailySalesEmailResponse(**result)
