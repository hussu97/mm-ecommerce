"""POS reporting endpoints — sales, operations and stock-cost reports."""

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
