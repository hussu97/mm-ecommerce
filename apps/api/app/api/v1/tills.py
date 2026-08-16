"""Tills, shifts and drawer operations — the cash-control surface of the POS."""

from __future__ import annotations

import uuid

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import ConflictError, ForbiddenError
from app.core.permissions import require
from app.models import Branch, Till
from app.models.user import User
from app.schemas.pos import (
    DrawerOperationCreate,
    DrawerOperationResponse,
    TillCloseRequest,
    TillOpenRequest,
    TillReport,
    TillResponse,
)
from app.services import audit_service, crud_service, till_service

router = APIRouter()


def _assert_can_touch(till: Till, user: User) -> None:
    """A cashier may only operate their own till; admins may operate any."""
    if till.user_id != user.id and not user.is_admin:
        raise ForbiddenError("This till belongs to another user")


# ─── Tills ────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[TillResponse])
async def list_tills(
    branch_id: uuid.UUID | None = None,
    business_date: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    stmt = select(Till)
    if not user.is_admin:
        stmt = stmt.where(Till.user_id == user.id)
    if branch_id:
        stmt = stmt.where(Till.branch_id == branch_id)
    if business_date:
        stmt = stmt.where(Till.business_date == business_date)
    if status_filter:
        stmt = stmt.where(Till.status == status_filter)
    stmt = stmt.order_by(Till.opened_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/current", response_model=TillResponse | None)
async def get_my_open_till(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """The caller's open till, or null — the POS calls this on every app launch."""
    return await till_service.get_open_till(db, user_id=user.id, branch_id=branch_id)


@router.post("/open", response_model=TillResponse, status_code=status.HTTP_201_CREATED)
async def open_till(
    request: Request,
    data: TillOpenRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    till = await till_service.open_till(
        db,
        user=user,
        branch=branch,
        device_id=data.device_id,
        opening_amount=data.opening_amount,
        notes=data.notes,
    )
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="till",
        entity_id=str(till.id),
        entity_label=f"{branch.name} {till.business_date}",
        admin=user,
        changes={"opening_amount": str(till.opening_amount)},
        request=request,
    )
    return till


@router.post("/{till_id}/close", response_model=TillResponse)
async def close_till(
    request: Request,
    till_id: uuid.UUID,
    data: TillCloseRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    till = await till_service.require_till(db, till_id)
    _assert_can_touch(till, user)
    till = await till_service.close_till(
        db,
        till=till,
        closed_by=user,
        closing_amount=data.closing_amount,
        notes=data.notes,
    )
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="till",
        entity_id=str(till.id),
        entity_label=f"Till {till.business_date}",
        admin=user,
        changes={
            "closed": True,
            "closing_amount": str(till.closing_amount),
            "variance": str(till.variance),
        },
        request=request,
    )
    return till


@router.get("/{till_id}", response_model=TillResponse)
async def get_till(
    till_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    till = await till_service.require_till(db, till_id)
    _assert_can_touch(till, user)
    return till


@router.get("/{till_id}/report", response_model=TillReport)
async def till_report(
    till_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """X-report while open, Z-report once closed."""
    till = await till_service.require_till(db, till_id)
    _assert_can_touch(till, user)
    return await till_service.build_report(db, till)


class SpotCheckRequest(BaseModel):
    counted_amount: Decimal = Field(ge=0)
    notes: str | None = None


class SpotCheckResult(BaseModel):
    counted_amount: Decimal
    expected_amount: Decimal
    variance: Decimal


@router.post("/{till_id}/spot-check", response_model=SpotCheckResult)
async def cash_spot_check(
    till_id: uuid.UUID,
    data: SpotCheckRequest,
    db: AsyncSession = Depends(get_db),
    # The message predates the `require` factory and is kept verbatim — the
    # register shows it to the cashier, and a refactor must not reword it.
    user: User = Depends(
        require(
            "pos.till.manage",
            message="You do not have permission to run a cash spot check",
        )
    ),
):
    """
    Count the drawer mid-shift without closing it.

    A manager wants to know the drawer balances now, not at midnight. The
    count is recorded as a zero-signed drawer operation so it appears in the
    audit trail and on the Z report's movement list, but does not itself move
    any cash — the expected balance is unchanged by having been looked at.
    """
    till = await till_service.require_till(db, till_id)
    if till.status != "open":
        raise ConflictError("This till is already closed")

    expected = await till_service.estimated_cash(db, till)
    variance = data.counted_amount - expected

    note = f"Counted {data.counted_amount}, expected {expected}, variance {variance}"
    await till_service.add_drawer_operation(
        db,
        till=till,
        user=user,
        op_type="spot_check",
        amount=data.counted_amount,
        notes=f"{note}. {data.notes}" if data.notes else note,
    )
    return SpotCheckResult(
        counted_amount=data.counted_amount,
        expected_amount=expected,
        variance=variance,
    )


# ─── Drawer operations ────────────────────────────────────────────────────────


@router.get(
    "/{till_id}/drawer-operations", response_model=list[DrawerOperationResponse]
)
async def list_drawer_operations(
    till_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    from app.models import DrawerOperation

    till = await till_service.require_till(db, till_id)
    _assert_can_touch(till, user)
    stmt = (
        select(DrawerOperation)
        .where(DrawerOperation.till_id == till_id)
        .order_by(DrawerOperation.recorded_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/{till_id}/drawer-operations",
    response_model=DrawerOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_drawer_operation(
    request: Request,
    till_id: uuid.UUID,
    data: DrawerOperationCreate,
    db: AsyncSession = Depends(get_db),
    # Ownership and authority are different questions, and this route was only
    # asking the first: `_assert_can_touch` says the till is yours, not that you
    # may pay money out of it. A pay-out or a no-sale is the drawer opening
    # outside a sale, which is why `pos.drawer.access` existed in the Foodics
    # matrix — it was just never wired to anything. Cash sales are unaffected:
    # they reach the ledger through `till_service` from the payment route, not
    # through here.
    user: User = Depends(require("pos.till.manage")),
):
    till = await till_service.require_till(db, till_id)
    _assert_can_touch(till, user)
    operation = await till_service.add_drawer_operation(
        db,
        till=till,
        user=user,
        op_type=data.type,
        amount=data.amount,
        reason_id=data.reason_id,
        notes=data.notes,
    )
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="drawer_operation",
        entity_id=str(operation.id),
        entity_label=f"{operation.type} {operation.amount}",
        admin=user,
        changes=data.model_dump(mode="json"),
        request=request,
    )
    return operation
