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
