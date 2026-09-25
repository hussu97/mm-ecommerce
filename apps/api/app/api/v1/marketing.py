"""Discounts, promotions and timed events."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import UnprocessableError
from app.core.permissions import require, require_any
from app.models import (
    Discount,
    Promotion,
    TimedEvent,
)
from app.models.user import User
from app.schemas.marketing import (  # noqa: F401 — re-exported for older imports
    AvailablePromotionResponse,
    DiscountCreate,
    DiscountResponse,
    DiscountUpdate,
    PromotionCreate,
    PromotionResponse,
    PromotionUpdate,
    PromotionUsageResponse,
    TimedEventCreate,
    TimedEventResponse,
    TimedEventUpdate,
    check_branch_modes,
)
from app.services.pos import auto_promotion_service

from .pos_config import build_crud_router

# A sixth copy of the `_require(user, permission)` helper used to sit here, and
# it was the strangest of the six: nothing in this file ever called it. Every
# route below is built by `build_crud_router`, which gates reads on
# `get_current_active_user` and writes on `get_admin_user` — so the copy was a
# permission check that looked like protection and enforced nothing.
#
# That is the failure mode `app.core.permissions` exists to end. A check written
# out imperatively per router can be forgotten (`pos_orders.add_item` shipped as
# a hole for exactly that reason) or, as here, written and never wired up, and
# neither shows up in a grep for "which routes demand what". The five live
# copies became `require(...)`/`ensure(...)`; this dead one is simply gone.
# Should these three entities ever need finer gating than "admin", they take a
# `Depends(require("marketing.<thing>"))` like everybody else.


# ─── Discounts ────────────────────────────────────────────────────────────────


discounts_router = build_crud_router(
    model=Discount,
    create_schema=DiscountCreate,
    update_schema=DiscountUpdate,
    response_schema=DiscountResponse,
    entity_type="discount",
)


# ─── Promotions ───────────────────────────────────────────────────────────────


def _prepare_promotion_write(
    payload: dict[str, Any], entity: Promotion | None
) -> dict[str, Any]:
    """Check the merged row's branch modes and keep `auto_apply` in step.

    The schemas validate what a payload can see; a partial update can still
    produce an incoherent row (coupon branches added to a promotion whose stored
    auto list already names that branch), so the rules run again here against
    the row as it will be stored.

    `auto_apply` is a compatibility column, always written as
    `bool(auto_branch_ids)`. A legacy `auto_apply: false` with no branch list
    turns the promotion off at every auto branch; `auto_apply: true` with no
    branch list is refused, because it no longer says *where*.
    """

    def merged(field: str, default: Any) -> Any:
        if field in payload:
            return payload[field]
        return getattr(entity, field, default) if entity is not None else default

    extra: dict[str, Any] = {}
    auto_ids = merged("auto_branch_ids", []) or []
    if "auto_branch_ids" not in payload and payload.get("auto_apply") is False:
        auto_ids = []
        extra["auto_branch_ids"] = []
    if payload.get("auto_apply") is True and not auto_ids:
        raise UnprocessableError(
            "auto_apply needs the branches it runs at — set auto_branch_ids"
        )
    try:
        check_branch_modes(
            auto_branch_ids=auto_ids,
            coupon_branch_ids=merged("coupon_branch_ids", []) or [],
            reward=merged("reward", None),
            trigger=merged("trigger", "spend"),
            sources=merged("sources", []) or [],
        )
    except ValueError as exc:
        raise UnprocessableError(str(exc)) from exc
    extra["auto_apply"] = bool(auto_ids)
    return extra


promotions_router = build_crud_router(
    model=Promotion,
    create_schema=PromotionCreate,
    update_schema=PromotionUpdate,
    response_schema=PromotionResponse,
    entity_type="promotion",
    prepare_write=_prepare_promotion_write,
)


#: Usage against each promotion's limit, mounted at `/promotions` AHEAD of the
#: CRUD router so `/promotions/usage` isn't read as `/promotions/{id}`.
promotion_usage_router = APIRouter()


@promotion_usage_router.get("/usage", response_model=list[PromotionUsageResponse])
async def promotion_usage(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_any("pos.register.access", "admin.settings.manage")),
):
    """Completed orders that carried each live promotion, against its limit."""
    promos = list(
        (await db.execute(select(Promotion).where(Promotion.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    used = await auto_promotion_service.usage_counts(db, [p.id for p in promos])
    return [
        PromotionUsageResponse(
            promotion_id=p.id,
            used=used.get(p.id, 0),
            usage_limit=p.usage_limit,
            exhausted=bool(p.usage_limit) and used.get(p.id, 0) >= p.usage_limit,
        )
        for p in promos
    ]


#: The register's view of counter promotions, mounted at `/pos/promotions` on
#: both the POS app and the main API.
pos_promotions_router = APIRouter()


@pos_promotions_router.get(
    "/available", response_model=list[AvailablePromotionResponse]
)
async def available_promotions(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("pos.register.access")),
):
    """Every counter promotion that runs at `branch_id`, auto and coupon, best
    first — with whether its schedule is live right now."""
    return [
        AvailablePromotionResponse(
            id=promo.id,
            name=promo.name,
            reward=promo.reward,
            reward_value=promo.reward_value,
            trigger_value=promo.trigger_value,
            category_ids=list(promo.category_ids or []),
            mode=mode,
            is_live_now=live,
        )
        for promo, mode, live in await auto_promotion_service.available_at(
            db, branch_id
        )
    ]


# ─── Timed events ─────────────────────────────────────────────────────────────


timed_events_router = build_crud_router(
    model=TimedEvent,
    create_schema=TimedEventCreate,
    update_schema=TimedEventUpdate,
    response_schema=TimedEventResponse,
    entity_type="timed_event",
)


__all__ = [
    "discounts_router",
    "pos_promotions_router",
    "promotion_usage_router",
    "promotions_router",
    "timed_events_router",
]
