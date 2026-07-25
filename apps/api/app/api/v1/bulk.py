from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.core.exceptions import BadRequestError
from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption
from app.models.product import Product
from app.models.promo_code import PromoCode
from app.models.user import User

router = APIRouter()

_ENTITY_MAP = {
    "products": Product,
    "categories": Category,
    "promo-codes": PromoCode,
    "modifiers": Modifier,
    "modifier-options": ModifierOption,
}


class BulkStatusRequest(BaseModel):
    ids: list[uuid.UUID]
    is_active: bool


@router.post("/{entity}/status")
async def bulk_update_status(
    entity: str = Path(...),
    body: BulkStatusRequest = ...,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Bulk activate or deactivate entities (admin only)."""
    model = _ENTITY_MAP.get(entity)
    if not model:
        raise BadRequestError(
            f"Unknown entity '{entity}'. Valid: {', '.join(_ENTITY_MAP)}"
        )

    stmt = (
        update(model)
        .where(model.id.in_(body.ids))
        .values(is_active=body.is_active)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    return {"updated": result.rowcount}


class BulkVisibilityRequest(BaseModel):
    ids: list[uuid.UUID]
    #: Omit a channel to leave it as it is — the two are set independently, so
    #: putting the coffee menu on the register must not also decide whether it
    #: belongs on a cake website.
    is_web_visible: bool | None = None
    is_pos_visible: bool | None = None


@router.post("/products/visibility")
async def bulk_update_visibility(
    body: BulkVisibilityRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """
    Put products on the website, the register, or both.

    Separate from the status endpoint because "not sold here" and "not sold at
    all" are different decisions: deactivating a product withdraws it
    everywhere, which is not what someone means when they take lattes off the
    cake site.
    """
    values = {
        field: getattr(body, field)
        for field in ("is_web_visible", "is_pos_visible")
        if getattr(body, field) is not None
    }
    if not values:
        raise BadRequestError("Choose at least one of the website or the register")
    if not body.ids:
        return {"updated": 0}

    stmt = (
        update(Product)
        .where(Product.id.in_(body.ids))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    return {"updated": result.rowcount}
