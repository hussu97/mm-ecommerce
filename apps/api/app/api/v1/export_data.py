from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.services import export_service

router = APIRouter()


def _csv_response(content: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/categories")
async def export_categories(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    content = await export_service.export_categories(db)
    return _csv_response(content, "categories.csv")


@router.get("/products")
async def export_products(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    content = await export_service.export_products(db)
    return _csv_response(content, "products.csv")


@router.get("/modifiers")
async def export_modifiers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    content = await export_service.export_modifiers(db)
    return _csv_response(content, "modifiers.csv")


@router.get("/modifier-options")
async def export_modifier_options(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    content = await export_service.export_modifier_options(db)
    return _csv_response(content, "modifier_options.csv")


@router.get("/product-modifiers")
async def export_product_modifiers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    content = await export_service.export_product_modifiers(db)
    return _csv_response(content, "product_modifiers.csv")
