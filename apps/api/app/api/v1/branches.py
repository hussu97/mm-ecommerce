"""Branches, their trading days, and the dine-in floor plan (sections + tables)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import trading_hours
from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import require
from app.models import (
    Branch,
    BranchBusinessDay,
    BranchHoliday,
    Device,
    PosTable,
    Section,
    Till,
    TillStatusEnum,
)
from app.models.user import User
from app.schemas.fulfilment import PickupBranchResponse
from app.schemas.pos import (
    BranchCreate,
    BranchHolidayCreate,
    BranchHolidayResponse,
    BranchHolidayUpdate,
    BranchResponse,
    BranchUpdate,
    BusinessDayResponse,
    SectionCreate,
    SectionResponse,
    SectionUpdate,
    TableCreate,
    TableResponse,
    TableUpdate,
    WeeklyHoursResponse,
    WeeklyHoursUpdate,
    WeeklyShift,
)
from app.services import (
    audit_service,
    branch_holiday_service,
    branch_hours_service,
    branch_hours_sync,
    crud_service,
)
from app.services.delivery import fulfilment_service
from app.services.pos import business_day_service

router = APIRouter()


# ─── Branches ─────────────────────────────────────────────────────────────────


@router.get("", response_model=list[BranchResponse])
async def list_branches(
    include_deleted: bool = False,
    include_inactive: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return await crud_service.list_all(
        db,
        Branch,
        include_deleted=include_deleted,
        include_inactive=include_inactive,
    )


@router.get("/pickup-points", response_model=list[PickupBranchResponse])
async def list_pickup_points(db: AsyncSession = Depends(get_db)):
    """
    The branches a customer may collect from. **Public.**

    Unauthenticated on purpose: this is checkout furniture, and a guest picking
    collection has to see the same list a signed-in customer does. It carries
    only what somebody driving there needs — name, address and city in both
    locales, a pin, opening hours and a phone number — and none of the
    operational columns on `BranchResponse`, which is why it is a different
    model rather than the same one with a different dependency.

    Declared above `/{branch_id}` so the literal path is matched before the
    UUID one gets a chance to reject it.
    """
    today = datetime.now(trading_hours.TZ).date()
    branches = await fulfilment_service.pickup_branches(db)
    out: list[PickupBranchResponse] = []
    for branch in branches:
        window = branch_hours_service.effective_window(
            await branch_hours_service.schedule(db, branch.id), today
        )
        out.append(PickupBranchResponse.of(branch, window))
    return out


@router.post("", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
async def create_branch(
    request: Request,
    data: BranchCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    if await crud_service.reference_taken(db, Branch, "reference", data.reference):
        raise ConflictError(f"Branch reference '{data.reference}' is already in use")
    branch = await crud_service.create(db, Branch, data)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="branch",
        entity_id=str(branch.id),
        entity_label=branch.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return branch


@router.get("/{branch_id}", response_model=BranchResponse)
async def get_branch(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return await crud_service.get_or_404(db, Branch, branch_id, include_deleted=True)


@router.put("/{branch_id}", response_model=BranchResponse)
async def update_branch(
    request: Request,
    branch_id: uuid.UUID,
    data: BranchUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    branch = await crud_service.get_or_404(db, Branch, branch_id)
    if data.reference and await crud_service.reference_taken(
        db, Branch, "reference", data.reference, exclude_id=branch_id
    ):
        raise ConflictError(f"Branch reference '{data.reference}' is already in use")
    branch = await crud_service.update(db, branch, data)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="branch",
        entity_id=str(branch_id),
        entity_label=branch.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return branch


@router.delete("/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(
    request: Request,
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    branch = await crud_service.get_or_404(db, Branch, branch_id)

    open_tills = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Till)
                .where(
                    Till.branch_id == branch_id,
                    Till.status == TillStatusEnum.OPEN.value,
                )
            )
        ).scalar_one()
    )
    if open_tills:
        raise ConflictError(
            f"Branch has {open_tills} open till(s). Close them before deleting."
        )

    label = branch.name
    await crud_service.soft_delete(db, branch)
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="branch",
        entity_id=str(branch_id),
        entity_label=label,
        admin=admin,
        changes={"deleted_id": str(branch_id)},
        request=request,
    )


# ─── Business days ────────────────────────────────────────────────────────────


@router.get("/{branch_id}/business-days", response_model=list[BusinessDayResponse])
async def list_business_days(
    branch_id: uuid.UUID,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    stmt = (
        select(BranchBusinessDay)
        .where(BranchBusinessDay.branch_id == branch_id)
        .order_by(BranchBusinessDay.business_date.desc())
        .limit(min(limit, 365))
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{branch_id}/business-days/current", response_model=BusinessDayResponse)
async def get_current_business_day(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    branch = await crud_service.get_or_404(db, Branch, branch_id)
    return await business_day_service.get_or_open(db, branch)


@router.post("/{branch_id}/business-days/close", response_model=BusinessDayResponse)
async def close_business_day(
    request: Request,
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    """End of day: freeze the Z-report totals for the branch's current trading day."""
    branch = await crud_service.get_or_404(db, Branch, branch_id)
    day = await business_day_service.close_current(db, branch, closed_by=admin)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="business_day",
        entity_id=str(day.id),
        entity_label=f"{branch.name} {day.business_date}",
        admin=admin,
        changes={"closed": True, "business_date": day.business_date},
        request=request,
    )
    return day


# ─── Weekly hours ─────────────────────────────────────────────────────────────
#
# The branch's per-weekday schedule — one shift a day, a weekday with no shift is
# closed. The single source of truth for when the branch trades: every reader
# resolves its window from it via `branch_hours_service`, a closed weekday reads
# through `branch_holiday_service` exactly like a holiday, and the marketplace
# fan-out sends it per portal. Editing it here moves all of them from the next
# request.


@router.get("/{branch_id}/weekly-hours", response_model=WeeklyHoursResponse)
async def get_weekly_hours(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
) -> WeeklyHoursResponse:
    """The branch's weekly schedule (source of truth for trading hours)."""
    await crud_service.get_or_404(db, Branch, branch_id)
    rows = await branch_hours_service.list_weekly(db, branch_id)
    return WeeklyHoursResponse(
        branch_id=str(branch_id),
        shifts=[
            WeeklyShift(weekday=r.weekday, opens=r.opens, closes=r.closes) for r in rows
        ],
    )


@router.put("/{branch_id}/weekly-hours", response_model=WeeklyHoursResponse)
async def set_weekly_hours(
    request: Request,
    branch_id: uuid.UUID,
    payload: WeeklyHoursUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
) -> WeeklyHoursResponse:
    """Replace the branch's weekly schedule (a weekday with no shift = closed).

    Changing this moves what every customer in the branch's zones is quoted and
    what the marketplaces are sent — the same reach as a holiday — from the next
    request, since every reader resolves its window from this schedule directly.
    """
    branch = await crud_service.get_or_404(db, Branch, branch_id)
    rows = await branch_hours_service.set_weekly(
        db, branch_id, [s.model_dump() for s in payload.shifts]
    )
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="branch",
        entity_id=str(branch_id),
        entity_label=f"{branch.name} weekly hours",
        admin=admin,
        changes={"shifts": [s.model_dump() for s in payload.shifts]},
        request=request,
    )
    return WeeklyHoursResponse(
        branch_id=str(branch_id),
        shifts=[
            WeeklyShift(weekday=r.weekday, opens=r.opens, closes=r.closes) for r in rows
        ],
    )


@router.post("/{branch_id}/sync-hours")
async def sync_branch_hours(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
) -> dict[str, object]:
    """Derive this branch's window from its weekly schedule now.

    The branch-hours loop does this hourly on its own; this is the manual trigger
    for right after editing the hours or adding a holiday, so the storefront and
    the integrators reflect the change immediately rather than at the next tick.
    """
    branch = (
        await db.execute(
            select(Branch)
            .where(Branch.id == branch_id)
            .options(selectinload(Branch.aggregator_maps))
        )
    ).scalar_one_or_none()
    if branch is None:
        raise NotFoundError("Branch not found")
    return await branch_hours_sync.sync_branch(db, branch)


# ─── Holidays ─────────────────────────────────────────────────────────────────
#
# Whole days a branch does not trade. Exceptions only — the shop works seven
# days a week, so there is no weekday rule and no row means open.
#
# These are not decoration on a settings page: `services/delivery_promise`
# reads them through `core.trading_hours`, so adding one here moves what every
# customer in that branch's zones is quoted from the next request onward.


@router.get("/{branch_id}/holidays", response_model=list[BranchHolidayResponse])
async def list_holidays(
    branch_id: uuid.UUID,
    include_past: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    """
    This branch's closed days, earliest first.

    Upcoming only by default. A closure that has already happened cannot move
    any promise still to be made, and keeping the screen to what is actionable
    is the point — the past ones stay in the table as the record of why a
    promise once read the way it did, and `include_past` shows them.
    """
    await crud_service.get_or_404(db, Branch, branch_id)
    return await branch_holiday_service.listing(
        db, branch_id, include_past=include_past
    )


@router.post(
    "/{branch_id}/holidays",
    response_model=BranchHolidayResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_holiday(
    request: Request,
    branch_id: uuid.UUID,
    data: BranchHolidayCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    """
    Close this branch for a day.

    One row per branch per date, enforced by a unique index; the same day
    submitted twice is a conflict rather than a second closure, because two
    rows saying the shop is shut is two records of one fact.
    """
    branch = await crud_service.get_or_404(db, Branch, branch_id)
    if await _holiday_on(db, branch_id, data.holiday_date) is not None:
        raise ConflictError(f"{branch.name} is already closed on {data.holiday_date}.")
    holiday = BranchHoliday(branch_id=branch_id, **data.model_dump())
    db.add(holiday)
    await db.flush()
    await db.refresh(holiday)
    await _log_holiday(
        db, request, admin, branch, holiday, action="CREATE", changes={"added": True}
    )
    return holiday


@router.put("/holidays/{holiday_id}", response_model=BranchHolidayResponse)
async def update_holiday(
    request: Request,
    holiday_id: uuid.UUID,
    data: BranchHolidayUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    holiday = await crud_service.get_or_404(db, BranchHoliday, holiday_id)
    before = {"holiday_date": holiday.holiday_date, "name": holiday.name}
    fields = data.model_dump(exclude_unset=True)
    moved_to = fields.get("holiday_date")
    if moved_to and moved_to != holiday.holiday_date:
        clash = await _holiday_on(db, holiday.branch_id, moved_to)
        if clash is not None:
            raise ConflictError(f"This branch is already closed on {moved_to}.")
    for field, value in fields.items():
        setattr(holiday, field, value)
    await db.flush()
    await db.refresh(holiday)

    branch = await crud_service.get_or_404(db, Branch, holiday.branch_id)
    await _log_holiday(
        db,
        request,
        admin,
        branch,
        holiday,
        action="UPDATE",
        changes={
            "from": before,
            "to": {"holiday_date": holiday.holiday_date, "name": holiday.name},
        },
    )
    return holiday


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    request: Request,
    holiday_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.branches.manage")),
):
    """
    Reopen the branch on that day.

    A hard delete rather than a soft one, unlike most of this router. A closure
    is a statement about one date and either stands or does not; a
    `deleted_at`-shaped closure would be a row saying the shop is shut that the
    promise has to be trusted to ignore, which is one more thing to get wrong
    than simply not having the row.
    """
    holiday = await crud_service.get_or_404(db, BranchHoliday, holiday_id)
    branch = await crud_service.get_or_404(db, Branch, holiday.branch_id)
    await _log_holiday(
        db,
        request,
        admin,
        branch,
        holiday,
        action="DELETE",
        changes={"removed": holiday.holiday_date},
    )
    await db.delete(holiday)
    await db.flush()


async def _holiday_on(
    db: AsyncSession, branch_id: uuid.UUID, day: str
) -> BranchHoliday | None:
    return (
        await db.execute(
            select(BranchHoliday).where(
                BranchHoliday.branch_id == branch_id,
                BranchHoliday.holiday_date == day,
            )
        )
    ).scalar_one_or_none()


async def _log_holiday(
    db: AsyncSession,
    request: Request,
    admin: User,
    branch: Branch,
    holiday: BranchHoliday,
    *,
    action: str,
    changes: dict,
) -> None:
    """
    Closures are audited like a status change, not like a settings tweak.

    Somebody will one day ask why a week of orders quoted three days out, and
    the answer is a row somebody added on a Tuesday afternoon.
    """
    await audit_service.log_action(
        db,
        action=action,
        entity_type="branch_holiday",
        entity_id=str(holiday.id),
        entity_label=f"{branch.name} — {holiday.holiday_date} {holiday.name}",
        admin=admin,
        changes=changes,
        request=request,
    )


# ─── Sections ─────────────────────────────────────────────────────────────────


@router.get("/{branch_id}/sections", response_model=list[SectionResponse])
async def list_sections(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    sections = await crud_service.list_all(
        db,
        Section,
        filters=[Section.branch_id == branch_id],
        options=[selectinload(Section.tables)],
    )
    payload: list[SectionResponse] = []
    for section in sections:
        item = SectionResponse.model_validate(section)
        item.tables = [
            TableResponse.model_validate(t)
            for t in section.tables
            if t.deleted_at is None
        ]
        payload.append(item)
    return payload


@router.post(
    "/{branch_id}/sections",
    response_model=SectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_section(
    branch_id: uuid.UUID,
    data: SectionCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    await crud_service.get_or_404(db, Branch, branch_id)
    section = await crud_service.create(
        db, Section, data, extra={"branch_id": branch_id}
    )
    return SectionResponse.model_validate(section)


@router.put("/sections/{section_id}", response_model=SectionResponse)
async def update_section(
    section_id: uuid.UUID,
    data: SectionUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    section = await crud_service.get_or_404(db, Section, section_id)
    section = await crud_service.update(db, section, data)
    return SectionResponse.model_validate(section)


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    section = await crud_service.get_or_404(db, Section, section_id)
    await crud_service.soft_delete(db, section)


# ─── Tables ───────────────────────────────────────────────────────────────────


@router.get("/sections/{section_id}/tables", response_model=list[TableResponse])
async def list_tables(
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    return await crud_service.list_all(
        db, PosTable, filters=[PosTable.section_id == section_id]
    )


@router.post(
    "/sections/{section_id}/tables",
    response_model=TableResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_table(
    section_id: uuid.UUID,
    data: TableCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    await crud_service.get_or_404(db, Section, section_id)
    return await crud_service.create(
        db, PosTable, data, extra={"section_id": section_id}
    )


@router.put("/tables/{table_id}", response_model=TableResponse)
async def update_table(
    table_id: uuid.UUID,
    data: TableUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    table = await crud_service.get_or_404(db, PosTable, table_id)
    return await crud_service.update(db, table, data)


@router.delete("/tables/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    table_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    table = await crud_service.get_or_404(db, PosTable, table_id)
    await crud_service.soft_delete(db, table)


# ─── Devices attached to a branch ─────────────────────────────────────────────


@router.get("/{branch_id}/device-count")
async def branch_device_count(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.branches.manage")),
):
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Device)
                .where(Device.branch_id == branch_id, Device.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    return {"branch_id": str(branch_id), "device_count": total}
