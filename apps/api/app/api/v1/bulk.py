from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy import String, case, cast, func, update
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete_pattern
from app.core.deps import get_db
from app.core.permissions import require
from app.core.exceptions import BadRequestError
from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption
from app.models.product import Product
from app.models.promo_code import PromoCode
from app.models.user import User
from app.schemas.product import SalesChannel

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
    _admin: User = Depends(require("catalogue.manage")),
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
    #: The channel being added or removed, and which way. One channel at a
    #: time on purpose: putting the coffee menu on the register must not also
    #: decide whether it belongs on a cake website, and a request that sent
    #: the whole list would silently overwrite the other channel for every
    #: product in the selection.
    channel: SalesChannel
    #: True adds the channel to each product, false takes it away.
    enabled: bool


@router.post("/products/visibility")
async def bulk_update_visibility(
    body: BulkVisibilityRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
):
    """
    Put products on the website or the register, or take them off.

    Separate from the status endpoint because "not sold here" and "not sold at
    all" are different decisions: deactivating a product withdraws it
    everywhere, which is not what someone means when they take lattes off the
    cake site.
    """
    if not body.ids:
        return {"updated": 0}

    channel = body.channel
    if body.enabled:
        # `|| ` would append a second copy on a product that already sells
        # there; the guard keeps the list a set.
        new_channels = case(
            (
                Product.sales_channels.contains([channel]),
                Product.sales_channels,
            ),
            else_=Product.sales_channels + cast(array([channel]), ARRAY(String)),
        )
    else:
        new_channels = func.array_remove(Product.sales_channels, channel)

    stmt = (
        update(Product)
        .where(Product.id.in_(body.ids))
        .values(sales_channels=new_channels)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await cache_delete_pattern("categories:channel:*")
    return {"updated": result.rowcount}
