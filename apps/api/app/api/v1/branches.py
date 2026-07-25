"""Branches, their trading days, and the dine-in floor plan (sections + tables)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.core.exceptions import ConflictError
from app.models import (
    Branch,
    BranchBusinessDay,
    Device,
    PosTable,
    Section,
    Till,
    TillStatusEnum,
)
from app.models.user import User
from app.schemas.pos import (
    BranchCreate,
    BranchResponse,
    BranchUpdate,
    BusinessDayResponse,
    SectionCreate,
    SectionResponse,
    SectionUpdate,
    TableCreate,
    TableResponse,
    TableUpdate,
)
from app.services import audit_service, business_day_service, crud_service

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


@router.post("", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
async def create_branch(
    request: Request,
    data: BranchCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
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
    admin: User = Depends(get_admin_user),
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
    admin: User = Depends(get_admin_user),
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
    _: User = Depends(get_admin_user),
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
    _: User = Depends(get_admin_user),
):
    branch = await crud_service.get_or_404(db, Branch, branch_id)
    return await business_day_service.get_or_open(db, branch)


@router.post("/{branch_id}/business-days/close", response_model=BusinessDayResponse)
async def close_business_day(
    request: Request,
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
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


# ─── Sections ─────────────────────────────────────────────────────────────────


@router.get("/{branch_id}/sections", response_model=list[SectionResponse])
async def list_sections(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
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
    _: User = Depends(get_admin_user),
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
    _: User = Depends(get_admin_user),
):
    section = await crud_service.get_or_404(db, Section, section_id)
    section = await crud_service.update(db, section, data)
    return SectionResponse.model_validate(section)


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    section = await crud_service.get_or_404(db, Section, section_id)
    await crud_service.soft_delete(db, section)


# ─── Tables ───────────────────────────────────────────────────────────────────


@router.get("/sections/{section_id}/tables", response_model=list[TableResponse])
async def list_tables(
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
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
    _: User = Depends(get_admin_user),
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
    _: User = Depends(get_admin_user),
):
    table = await crud_service.get_or_404(db, PosTable, table_id)
    return await crud_service.update(db, table, data)


@router.delete("/tables/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    table_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    table = await crud_service.get_or_404(db, PosTable, table_id)
    await crud_service.soft_delete(db, table)


# ─── Devices attached to a branch ─────────────────────────────────────────────


@router.get("/{branch_id}/device-count")
async def branch_device_count(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
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
