"""Couriers: who carries a zone's orders, and what we are charged for it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.permissions import require
from app.models.courier import Courier, UnbatchedPromiseEnum
from app.models.delivery_polygon import DeliveryPolygon
from app.models.user import User
from app.services import audit_service
from app.services.delivery import delivery_zone_service

from .schemas import CourierResponse, CourierUpdate

router = APIRouter()


# ── Couriers ──────────────────────────────────────────────────────────────────
#
# The delivery promise. Every zone is quoted straight from these numbers, and
# until now they had no way in that was not a migration.


async def _live_zone_counts(db: AsyncSession) -> dict[str, int]:
    """How many zones on the published map each courier currently carries."""
    version = await delivery_zone_service.get_active_version(db)
    if version is None:
        return {}
    rows = await db.execute(
        select(DeliveryPolygon.fulfilment_provider, func.count())
        .where(DeliveryPolygon.version_id == version.id)
        .group_by(DeliveryPolygon.fulfilment_provider)
    )
    return {provider: int(count) for provider, count in rows.all()}


@router.get("/couriers", response_model=list[CourierResponse])
async def list_couriers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """Every carrier and what it promises."""
    couriers = (
        (await db.execute(select(Courier).order_by(Courier.name))).scalars().all()
    )
    counts = await _live_zone_counts(db)
    return [CourierResponse.of(c, counts.get(c.code, 0)) for c in couriers]


@router.put("/couriers/{code}", response_model=CourierResponse)
async def update_courier(
    code: str,
    data: CourierUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Change what a courier promises.

    Refuses a `kind` of `minutes` with no minutes to quote, in either the body
    or the row it would leave behind. That combination is the one way to make
    the resolver fall back to its own literal — a number nobody chose, quoted
    to a customer as though somebody had.
    """
    courier = (
        await db.execute(select(Courier).where(Courier.code == code))
    ).scalar_one_or_none()
    if courier is None:
        raise NotFoundError(f"Courier '{code}' not found")

    kind = data.unbatched_promise_kind or courier.unbatched_promise_kind
    allowed = {member.value for member in UnbatchedPromiseEnum}
    if kind not in allowed:
        raise BadRequestError(
            f"Unknown promise kind '{kind}'. Allowed: {sorted(allowed)}"
        )
    minutes = (
        data.unbatched_promise_minutes
        if data.unbatched_promise_minutes is not None
        else courier.unbatched_promise_minutes
    )
    if kind == UnbatchedPromiseEnum.MINUTES.value and not minutes:
        raise BadRequestError(
            f"{courier.name} promises an hour rather than a day, so it needs a "
            "number of minutes. Set one, or switch it to next-day."
        )

    before = CourierResponse.of(courier, 0).model_dump(exclude={"zone_count"})
    # `exclude_unset`, so a field the client did not send is left alone.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(courier, field, value)
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="courier",
        entity_id=courier.code,
        entity_label=courier.name,
        admin=admin,
        changes={
            "from": before,
            "to": CourierResponse.of(courier, 0).model_dump(exclude={"zone_count"}),
        },
        request=request,
    )
    counts = await _live_zone_counts(db)
    return CourierResponse.of(courier, counts.get(courier.code, 0))
