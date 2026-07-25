"""Tills, shifts and drawer operations — the cash-control surface of the POS."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models import Branch, Shift, Till
from app.models.base import utcnow
from app.models.user import User
from app.schemas.pos import (
    DrawerOperationCreate,
    DrawerOperationResponse,
    ShiftClockInRequest,
    ShiftResponse,
    TillCloseRequest,
    TillOpenRequest,
    TillReport,
    TillResponse,
)
from app.services import audit_service, business_day_service, crud_service, till_service

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
    user: User = Depends(get_current_active_user),
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


# ─── Shifts ───────────────────────────────────────────────────────────────────


@router.get("/shifts/list", response_model=list[ShiftResponse])
async def list_shifts(
    branch_id: uuid.UUID | None = None,
    business_date: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    stmt = select(Shift)
    if not user.is_admin:
        stmt = stmt.where(Shift.user_id == user.id)
    if branch_id:
        stmt = stmt.where(Shift.branch_id == branch_id)
    if business_date:
        stmt = stmt.where(Shift.business_date == business_date)
    stmt = stmt.order_by(Shift.clocked_in_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/shifts/clock-in",
    response_model=ShiftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clock_in(
    data: ShiftClockInRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    target_id = data.user_id if (data.user_id and user.is_admin) else user.id

    open_shift = (
        (
            await db.execute(
                select(Shift).where(
                    Shift.user_id == target_id, Shift.clocked_out_at.is_(None)
                )
            )
        )
        .scalars()
        .first()
    )
    if open_shift is not None:
        raise ConflictError("This user is already clocked in")

    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    business_date = await business_day_service.current_business_date(db, branch)

    shift = Shift(
        branch_id=branch.id,
        user_id=target_id,
        business_date=business_date,
        clocked_in_at=utcnow(),
    )
    db.add(shift)
    await db.flush()
    await db.refresh(shift)
    return shift


@router.post("/shifts/{shift_id}/clock-out", response_model=ShiftResponse)
async def clock_out(
    shift_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    shift = await db.get(Shift, shift_id)
    if shift is None:
        raise NotFoundError("Shift not found")
    if shift.user_id != user.id and not user.is_admin:
        raise ForbiddenError("This shift belongs to another user")
    if shift.clocked_out_at is not None:
        raise ConflictError("This shift is already closed")

    open_till = await till_service.get_open_till(db, user_id=shift.user_id)
    if open_till is not None:
        raise ConflictError("Close your till before clocking out")

    shift.clocked_out_at = utcnow()
    await db.flush()
    await db.refresh(shift)
    return shift
