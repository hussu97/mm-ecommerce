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
from app.services import audit_service, delivery_zone_service

from .schemas import CourierResponse, CourierUpdate

router = APIRouter()


# ── Couriers ──────────────────────────────────────────────────────────────────
#
# The unbatched half of the delivery promise. A zone in no batch group — every
# noon Send zone, and every third-party one — is quoted straight from these two
# numbers, and until now neither had a way in that was not a migration.


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


def _assert_rates_belong_here(courier: Courier, data: "CourierUpdate") -> None:
    """
    Refuse a commission on a courier MM dispatches itself.

    Those are billed per booking, and the amount lands on
    `order_deliveries.cost_total`, which `order_economics` already subtracts. A
    percentage here as well would take the same cost off the same order twice —
    and the resulting margin would be wrong in the direction nobody checks,
    because a figure that looks worse than expected gets believed.
    """
    if courier.is_aggregator:
        return
    sent = data.model_dump(exclude_unset=True)
    offending = [
        field
        for field in (
            "commission_percent",
            "commission_fixed",
            "payment_fee_percent",
            "payment_fee_fixed",
        )
        if sent.get(field) is not None
    ]
    if offending:
        raise BadRequestError(
            f"{courier.name} is a courier MM dispatches, not a marketplace. "
            "What it charges is recorded per booking on the order's delivery "
            "record; a percentage here would subtract that cost a second time."
        )


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

    _assert_rates_belong_here(courier, data)

    before = CourierResponse.of(courier, 0).model_dump(exclude={"zone_count"})
    # `exclude_unset`, not `exclude_none`. A rate has three states — a number,
    # zero, and "nobody has told us" — and under `exclude_none` the third was
    # unreachable: having once typed 25 into Talabat by mistake, there was no
    # way back to unknown, only to a zero that claims the channel is free. What
    # the client did not send is still left alone, which is all `exclude_none`
    # was ever there for.
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
