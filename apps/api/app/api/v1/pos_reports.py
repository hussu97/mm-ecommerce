"""POS reporting endpoints — sales, payments, tax, operations and inventory."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import BadRequestError, ForbiddenError
from app.models.user import User
from app.services import pos_reports_service

router = APIRouter()

DimensionLiteral = Literal[
    "product", "category", "order_type", "source", "business_date", "staff", "hour"
]


def _require(user: User, permission: str) -> None:
    if not user.can(permission):
        raise ForbiddenError(f"You do not have permission to {permission}")


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
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.sales")
    return await pos_reports_service.sales_summary(db, **window.kwargs)


@router.get("/sales/by")
async def sales_by(
    dimension: DimensionLiteral,
    limit: int = Query(100, ge=1, le=1000),
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Sales grouped by product, category, order type, source, day, staff or hour."""
    _require(user, "reports.sales")
    return await pos_reports_service.sales_by_dimension(
        db, dimension=dimension, limit=limit, **window.kwargs
    )


@router.get("/payments")
async def payments(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.sales")
    return await pos_reports_service.payments_report(db, **window.kwargs)


@router.get("/taxes")
async def taxes(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """VAT return input: taxable base and tax collected per rate."""
    _require(user, "reports.other")
    return await pos_reports_service.tax_report(db, **window.kwargs)


@router.get("/voids-returns")
async def voids_and_returns(
    limit: int = Query(200, ge=1, le=1000),
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.other")
    return await pos_reports_service.voids_and_returns(db, limit=limit, **window.kwargs)


@router.get("/tills")
async def tills(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.other")
    return await pos_reports_service.tills_report(db, **window.kwargs)


@router.get("/drawer-operations")
async def drawer_operations(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.other")
    return await pos_reports_service.drawer_operations_report(db, **window.kwargs)


@router.get("/inventory/valuation")
async def inventory_valuation(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Stock value on hand plus everything below its reorder point."""
    _require(user, "reports.inventory_levels")
    return await pos_reports_service.inventory_valuation(db, branch_id=branch_id)


@router.get("/inventory/cost-of-goods")
async def cost_of_goods(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.cost_analysis")
    return await pos_reports_service.cost_of_goods(db, **window.kwargs)


@router.get("/menu-engineering")
async def menu_engineering(
    limit: int = Query(200, ge=1, le=1000),
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Star / plough-horse / puzzle / dog classification by volume and margin."""
    _require(user, "reports.menu_cost")
    return await pos_reports_service.menu_engineering(db, limit=limit, **window.kwargs)


@router.get("/speed-of-service")
async def speed_of_service(
    window: _Window = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Kitchen acknowledge, prep and total times over the window."""
    _require(user, "reports.other")
    return await pos_reports_service.speed_of_service(db, **window.kwargs)
