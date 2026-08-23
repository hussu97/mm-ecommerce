from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.schemas.import_data import ImportResult
from app.services import image_warm_service, import_service

router = APIRouter()


async def _parse_csv(upload: UploadFile) -> list[dict]:
    content = await upload.read()
    text = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


@router.post("/categories", response_model=ImportResult)
async def import_categories(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.data.manage")),
):
    """Import categories from Foodics CSV export."""
    rows = await _parse_csv(file)
    result = await import_service.import_categories(db, rows)
    image_warm_service.warm_in_background(result.image_urls)
    return result


@router.post("/products", response_model=ImportResult)
async def import_products(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.data.manage")),
):
    """Import products from Foodics CSV export."""
    rows = await _parse_csv(file)
    result = await import_service.import_products(db, rows)
    # A Foodics export brings in the whole catalogue at once, which is exactly
    # the case that used to leave every product cold until a customer opened it.
    image_warm_service.warm_in_background(result.image_urls)
    return result


@router.post("/modifiers", response_model=ImportResult)
async def import_modifiers(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.data.manage")),
):
    """Import modifiers from Foodics CSV export."""
    rows = await _parse_csv(file)
    return await import_service.import_modifiers(db, rows)


@router.post("/modifier-options", response_model=ImportResult)
async def import_modifier_options(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.data.manage")),
):
    """Import modifier options from Foodics CSV export."""
    rows = await _parse_csv(file)
    return await import_service.import_modifier_options(db, rows)


@router.post("/product-modifiers", response_model=ImportResult)
async def import_product_modifiers(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("admin.data.manage")),
):
    """Import product-modifier assignments from Foodics CSV export."""
    rows = await _parse_csv(file)
    return await import_service.import_product_modifiers(db, rows)
