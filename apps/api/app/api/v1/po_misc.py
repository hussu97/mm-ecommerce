"""Misc purchase-order line categories and period presets.

The console manages both lists (``/inventory/po-misc-categories``,
``/inventory/po-misc-periods``); the till reads them for its create-a-PO form
(``/pos/purchase-orders/misc-categories`` and ``/misc-periods``). Admin-only
categories never reach the till, and in the console only holders of
``inventory.purchase_orders.restricted_misc`` see or set them — the rules live
in ``po_misc_service``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.inventory import PurchaseOrderMiscPeriod
from app.models.user import User
from app.schemas.inventory import (
    PurchaseOrderMiscCategoryCreate,
    PurchaseOrderMiscCategoryResponse,
    PurchaseOrderMiscCategoryUpdate,
    PurchaseOrderMiscPeriodCreate,
    PurchaseOrderMiscPeriodResponse,
    PurchaseOrderMiscPeriodUpdate,
)
from app.services import audit_service
from app.services.inventory import po_misc_service
from app.services.pos import business_day_service

_MANAGE = "inventory.purchase_orders.manage"

po_misc_categories_router = APIRouter()
po_misc_periods_router = APIRouter()
#: Mounted on the till's ``/pos/purchase-orders`` ahead of its ``/{po_id}``
#: routes, whose UUID path would otherwise swallow these paths with a 422.
pos_po_misc_router = APIRouter()


async def _serialise_periods(
    db: AsyncSession, periods: list[PurchaseOrderMiscPeriod]
) -> list[PurchaseOrderMiscPeriodResponse]:
    today = business_day_service.shop_today(
        await business_day_service.resolve_timezone(db)
    )
    out = []
    for period in periods:
        start, end = po_misc_service.default_range(period.unit, period.length, today)
        out.append(
            PurchaseOrderMiscPeriodResponse(
                id=period.id,
                name=period.name,
                unit=period.unit,
                length=period.length,
                is_default=period.is_default,
                display_order=period.display_order,
                deleted_at=period.deleted_at,
                default_from=start,
                default_to=end,
            )
        )
    return out


async def _audit(
    db: AsyncSession,
    request: Request,
    user: User,
    *,
    action: str,
    entity_type: str,
    entity,
    changes: dict | None = None,
) -> None:
    await audit_service.log_action(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity.id),
        entity_label=entity.name,
        admin=user,
        changes=changes,
        request=request,
    )


# ─── Categories (console) ─────────────────────────────────────────────────────


@po_misc_categories_router.get(
    "", response_model=list[PurchaseOrderMiscCategoryResponse]
)
async def list_misc_categories(
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """Every category, alphabetical — admin-only ones only for their holders."""
    return await po_misc_service.list_categories(
        db,
        include_gated=po_misc_service.can_see_gated(user),
        include_deleted=include_deleted,
    )


@po_misc_categories_router.post(
    "",
    response_model=PurchaseOrderMiscCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_misc_category(
    request: Request,
    data: PurchaseOrderMiscCategoryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    category = await po_misc_service.create_category(db, user, data)
    await _audit(
        db,
        request,
        user,
        action="CREATE",
        entity_type="po_misc_category",
        entity=category,
        changes={"created": data.model_dump(mode="json")},
    )
    return category


@po_misc_categories_router.put(
    "/{category_id}", response_model=PurchaseOrderMiscCategoryResponse
)
async def update_misc_category(
    request: Request,
    category_id: uuid.UUID,
    data: PurchaseOrderMiscCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    category = await po_misc_service.update_category(db, user, category_id, data)
    await _audit(
        db,
        request,
        user,
        action="UPDATE",
        entity_type="po_misc_category",
        entity=category,
        changes={"after": data.model_dump(mode="json", exclude_unset=True)},
    )
    return category


@po_misc_categories_router.delete(
    "/{category_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_misc_category(
    request: Request,
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """Soft delete: it leaves every picker; lines that already use it keep it."""
    category = await po_misc_service.delete_category(db, user, category_id)
    await _audit(
        db,
        request,
        user,
        action="DELETE",
        entity_type="po_misc_category",
        entity=category,
    )


@po_misc_categories_router.post(
    "/{category_id}/restore", response_model=PurchaseOrderMiscCategoryResponse
)
async def restore_misc_category(
    request: Request,
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    category = await po_misc_service.restore_category(db, user, category_id)
    await _audit(
        db,
        request,
        user,
        action="UPDATE",
        entity_type="po_misc_category",
        entity=category,
        changes={"after": {"restored": True}},
    )
    return category


# ─── Period presets (console) ─────────────────────────────────────────────────


@po_misc_periods_router.get("", response_model=list[PurchaseOrderMiscPeriodResponse])
async def list_misc_periods(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    return await _serialise_periods(db, await po_misc_service.list_periods(db))


@po_misc_periods_router.post(
    "",
    response_model=PurchaseOrderMiscPeriodResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_misc_period(
    request: Request,
    data: PurchaseOrderMiscPeriodCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    period = await po_misc_service.create_period(db, data)
    await _audit(
        db,
        request,
        user,
        action="CREATE",
        entity_type="po_misc_period",
        entity=period,
        changes={"created": data.model_dump(mode="json")},
    )
    return (await _serialise_periods(db, [period]))[0]


@po_misc_periods_router.put(
    "/{period_id}", response_model=PurchaseOrderMiscPeriodResponse
)
async def update_misc_period(
    request: Request,
    period_id: uuid.UUID,
    data: PurchaseOrderMiscPeriodUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    period = await po_misc_service.update_period(db, period_id, data)
    await _audit(
        db,
        request,
        user,
        action="UPDATE",
        entity_type="po_misc_period",
        entity=period,
        changes={"after": data.model_dump(mode="json", exclude_unset=True)},
    )
    return (await _serialise_periods(db, [period]))[0]


@po_misc_periods_router.delete("/{period_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_misc_period(
    request: Request,
    period_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    period = await po_misc_service.delete_period(db, period_id)
    await _audit(
        db,
        request,
        user,
        action="DELETE",
        entity_type="po_misc_period",
        entity=period,
    )


# ─── The till ─────────────────────────────────────────────────────────────────


@pos_po_misc_router.get(
    "/misc-categories", response_model=list[PurchaseOrderMiscCategoryResponse]
)
async def pos_list_misc_categories(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """Live categories for the till's picker, alphabetical. Never admin-only
    ones, whoever is signed in."""
    return await po_misc_service.list_categories(
        db, include_gated=False, include_inactive=False
    )


@pos_po_misc_router.get(
    "/misc-periods", response_model=list[PurchaseOrderMiscPeriodResponse]
)
async def pos_list_misc_periods(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    return await _serialise_periods(db, await po_misc_service.list_periods(db))
