"""Record an aggregator order's rider into the SAME tables an MM courier uses —
`order_deliveries` (the per-order snapshot) and `order_drivers` (the stint
ledger) — so the marketplace is modelled as a 'fulfilment courier' and the
order-details page renders ONE fulfilment section for every order type instead of
a separate card fed by the `orders.aggregator_driver_*` columns.

The row is deliberately INERT to the dispatch / batching / tracking machinery: it
carries no `courier_order_id`, `batch_id`, `dispatchable_at`, `next_attempt_at`,
price quote or GPS. Every sweep that could book or poll a courier is gated on
exactly those, or on `provider == lalamove`, or on `source == online` (verified
2026-08-30 across batching_service/driver_tracking/driver_routing/arrival). So an
aggregator delivery row is a record of who carried the order — nothing the shop
can dispatch, chase or refund.

Written on the aggregator side (promotion + the GrubOps ingest), never by the
courier webhooks. `orders.aggregator_driver_*` stay populated in parallel for now
(readers migrate first); a later change retires them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import DeliveryMethodEnum, Order
from app.models.order_delivery import OrderDelivery
from app.services.couriers import courier_catalog
from app.services.delivery import driver_assignment


def _num(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


async def record_aggregator_fulfilment(
    db: AsyncSession,
    order: Order,
    *,
    channel: str | None,
    driver_name: str | None = None,
    driver_phone: str | None = None,
    driver_status: str | None = None,
    cancel_reason: str | None = None,
    delivery_fee: Any = None,
) -> None:
    """Upsert the marketplace-rider delivery row (and rider stint) for one order.

    A no-op for a pickup order — there is no rider to record. Idempotent: one row
    per order (`uq_order_deliveries_order`), and the rider goes through
    `driver_assignment.record`, which fills gaps without opening a duplicate stint.
    """
    if order.delivery_method != DeliveryMethodEnum.DELIVERY.value:
        return

    # The marketplace as a courier code (careem/talabat/…) so the panel shows its
    # badge; a generic `aggregator` when the channel name is unrecognised.
    provider = courier_catalog.code_for_channel(channel) or "aggregator"

    delivery = await db.scalar(
        select(OrderDelivery).where(OrderDelivery.order_id == order.id)
    )
    if delivery is None:
        delivery = OrderDelivery(
            order_id=order.id,
            provider=provider,
            fee_charged=_num(delivery_fee),
        )
        db.add(delivery)
        await db.flush()
    else:
        # Keep the carrier current; never blank a known one back to the fallback.
        if provider != "aggregator":
            delivery.provider = provider
        if delivery.fee_charged is None:
            delivery.fee_charged = _num(delivery_fee)

    # The marketplace's own verbatim words, in the same columns an MM courier's
    # lifecycle uses (both are unconstrained provider vocabulary by design).
    if driver_status:
        delivery.courier_status = driver_status
    if cancel_reason:
        delivery.cancel_reason = cancel_reason

    # The rider, through the shared ledger + snapshot bookkeeping. No stable driver
    # id from a marketplace, so identity is by phone — which `record` tolerates.
    if driver_name or driver_phone:
        await driver_assignment.record(
            db,
            delivery,
            driver_assignment.Driver(name=driver_name, phone=driver_phone),
        )
