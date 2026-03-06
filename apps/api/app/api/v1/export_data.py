from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.language import Language
from app.models.order import OrderStatusEnum
from app.models.user import User
from app.services import export_service

router = APIRouter()


def _csv_response(content: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def _get_language_codes(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(Language.code)
        .where(Language.is_active == True, Language.is_default == False)  # noqa: E712
        .order_by(Language.display_order)
    )
    return list(result.scalars().all())


@router.get("/orders")
async def export_orders(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    status: Optional[OrderStatusEnum] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    if start_date is None and end_date is None:
        end_date = date.today()
        start_date = end_date - timedelta(days=30)
    content = await export_service.export_orders(db, start_date, end_date, status)
    return _csv_response(content, "orders.csv")


@router.get("/categories")
async def export_categories(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    languages = await _get_language_codes(db)
    content = await export_service.export_categories(db, languages)
    return _csv_response(content, "categories.csv")


@router.get("/products")
async def export_products(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    languages = await _get_language_codes(db)
    content = await export_service.export_products(db, languages)
    return _csv_response(content, "products.csv")


@router.get("/modifiers")
async def export_modifiers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    languages = await _get_language_codes(db)
    content = await export_service.export_modifiers(db, languages)
    return _csv_response(content, "modifiers.csv")


@router.get("/modifier-options")
async def export_modifier_options(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    languages = await _get_language_codes(db)
    content = await export_service.export_modifier_options(db, languages)
    return _csv_response(content, "modifier_options.csv")


@router.get("/product-modifiers")
async def export_product_modifiers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    content = await export_service.export_product_modifiers(db)
    return _csv_response(content, "product_modifiers.csv")
