"""Scoping, the dimension vocabulary, and the label lookups every report shares."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.device import Device
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import (
    PosOrderStatusEnum,
)
from app.models.pos_table import PosTable
from app.models.till import Till
from app.models.user import User
from app.services.couriers import courier_catalog

ZERO = Decimal("0.00")


def _scope(
    stmt: Select[Any],
    *,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
) -> Select[Any]:
    """Apply the standard branch + business-date window to an order query."""
    stmt = stmt.where(Order.is_pos.is_(True))
    if branch_id:
        stmt = stmt.where(Order.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Order.business_date >= date_from)
    if date_to:
        stmt = stmt.where(Order.business_date <= date_to)
    return stmt


CLOSED = PosOrderStatusEnum.CLOSED.value


VOID = PosOrderStatusEnum.VOID.value


#: What counts as a completed POS sale, across every sales report.
#:
#: A counter check is done when the till closes it (`pos_status = closed`), and a
#: GrubOps order maps its own `delivered` to the same `closed` on ingest — so the
#: aggregators were already in. A website order is the exception the reports used
#: to miss entirely: it is paid online and fulfilled by a courier, so its
#: `pos_status` never leaves `active` and only its e-commerce `status` reaches
#: `delivered`. Counting that here is what puts Website revenue on the same
#: footing as a Talabat order, which is the whole point of a per-channel report.
#:
#: Cancellations and refunds are excluded by construction: a cancelled website
#: order is `cancelled`, not `delivered`, and a voided counter check is
#: `pos_status = void`, not `closed`.
_COMPLETED_SALE = or_(
    Order.pos_status == CLOSED,
    and_(Order.source == "online", Order.status == OrderStatusEnum.DELIVERED.value),
)


#: When an order carries no cashier or terminal of its own — every aggregator and
#: website order, because nobody rings them up — this reads back "who was online
#: at the POS" as the till that was open at the order's branch across the moment
#: it arrived. Correlated per order (a LATERAL), branch-scoped, and when more than
#: one device is trading the most recently opened till wins. An order that came in
#: while no till was open stays unattributed, which is the honest answer.
def _covering_till():
    when = func.coalesce(Order.closed_at, Order.created_at)
    return (
        select(
            Till.user_id.label("user_id"),
            Till.device_id.label("device_id"),
        )
        .where(
            Till.branch_id == Order.branch_id,
            Till.opened_at <= when,
            or_(Till.closed_at.is_(None), Till.closed_at >= when),
        )
        .order_by(Till.opened_at.desc())
        .limit(1)
        .lateral("covering_till")
    )


def _channel_logo(key: Any) -> str | None:
    """The badge for a channel row, or None for the shop's own two channels."""
    if key is None or str(key) in {"online", "cashier"}:
        return None
    code = courier_catalog.code_for_channel(str(key))
    return courier_catalog.logo_url_for(code) if code else None


#: How an order's channel is grouped once each marketplace counts separately.
#:
#: `source` alone answers `online` / `cashier` / `aggregator`, and that third
#: bucket is the problem: it is five different businesses charging five
#: different commissions, reported as one line. A manager comparing what the
#: shop keeps per channel — the question the fee columns exist to answer — needs
#: Talabat apart from Noon Food, because that is the comparison that decides
#: which of them is worth being on.
#:
#: Grouped on the marketplace's own display name rather than our courier code,
#: because that is what the column holds; `_channel_labels` maps it to the code
#: and its badge on the way out, through the same `courier_catalog` the receipt
#: and the order list use, so all three agree about which marketplace an order
#: came from.
_CHANNEL_COLUMN = case(
    (Order.source == "aggregator", Order.aggregator_channel),
    else_=Order.source,
)


#: Dimensions that group the order rows themselves.
_ORDER_DIMENSIONS = {
    "order_type": Order.order_type,
    "source": Order.source,
    "channel": _CHANNEL_COLUMN,
    "business_date": Order.business_date,
    # Foodics separates "cashier" (who closed it) from "creator" (who rang it
    # up); on a single-terminal shift they are the same person, on a busy one
    # they are not, and the split is how a manager spots a hand-off.
    "staff": Order.closer_id,
    "cashier": Order.closer_id,
    "creator": Order.creator_id,
    "driver": Order.driver_id,
    "customer": Order.user_id,
    "branch": Order.branch_id,
    "table": Order.table_id,
    # Which POS machine rang it up. A branch with three tills reports as one row
    # under "branch"; a manager comparing counters — or looking for the terminal
    # that stopped selling at four o'clock — needs them apart.
    "device": Order.device_id,
    "hour": func.to_char(func.coalesce(Order.closed_at, Order.created_at), "HH24"),
}


#: Dimensions that live on a child row, so they need a join and a sum of the
#: child's own amount rather than the order total — a check with two discounts
#: must not count its full value against each of them.
_LINE_DIMENSIONS = {"discount", "charge", "tax"}


#: Discounts carry where they came from, so coupon, promotion and timed-event
#: are the same grouping narrowed to one source.
_DISCOUNT_SOURCES = {
    "coupon": "coupon",
    "promotion": "promotion",
    "timed_event": "timed_event",
}


#: Dimensions reached through the table an order was seated at.
_TABLE_DIMENSIONS = {"section", "revenue_center"}


#: Tags attached to something other than the order or the product.
_ENTITY_TAG_DIMENSIONS = {
    "branch_tag": "branch",
    "product_tag": "product",
    "order_tag": "order",
}


SUPPORTED_DIMENSIONS = (
    set(_ORDER_DIMENSIONS)
    | _LINE_DIMENSIONS
    | set(_DISCOUNT_SOURCES)
    | _TABLE_DIMENSIONS
    | set(_ENTITY_TAG_DIMENSIONS)
    | {"product", "category", "modifier_option", "delivery_zone"}
)


async def _staff_labels(db: AsyncSession, rows: Sequence[Any]) -> dict[str, str]:
    """Display names for grouped user ids, keyed by id string."""
    ids = {r[0] for r in rows if r[0] is not None}
    if not ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {str(u.id): (u.display_name or u.email) for u in users}


def _channel_labels(rows: Sequence[Any]) -> dict[str, str]:
    """
    Channel keys in the words the shop uses out loud.

    `online` and `cashier` are ours and are simply renamed. Everything else is a
    marketplace display name straight from GrubOps — "Keeta 2.0", "Noon" — and
    is resolved through `courier_catalog`, the one place that knows those names
    map to `keeta` and `noon_food`. An unrecognised marketplace keeps its own
    name rather than becoming "Unknown": a new aggregator nobody has mapped yet
    is still a real row of real money.
    """
    out: dict[str, str] = {}
    for row in rows:
        key = row[0]
        if key is None:
            continue
        key = str(key)
        if key == "online":
            out[key] = "Website"
        elif key == "cashier":
            out[key] = "Counter"
        else:
            code = courier_catalog.code_for_channel(key)
            out[key] = courier_catalog.COURIER_NAMES.get(code or "", key)
    return out


async def _labels_for(
    db: AsyncSession, dimension: str, rows: Sequence[Any]
) -> dict[str, str]:
    """Turn grouped foreign keys into names a human recognises."""
    if dimension == "channel":
        return _channel_labels(rows)

    ids = {r[0] for r in rows if r[0] is not None}
    if not ids:
        return {}

    if dimension in {"staff", "cashier", "creator", "driver", "customer"}:
        return await _staff_labels(db, rows)

    if dimension == "branch":
        branches = (
            (await db.execute(select(Branch).where(Branch.id.in_(ids)))).scalars().all()
        )
        return {str(b.id): b.name for b in branches}

    if dimension == "table":
        tables = (
            (await db.execute(select(PosTable).where(PosTable.id.in_(ids))))
            .scalars()
            .all()
        )
        return {str(t.id): t.name for t in tables}

    if dimension == "device":
        devices = (
            (await db.execute(select(Device).where(Device.id.in_(ids)))).scalars().all()
        )
        return {str(d.id): d.name for d in devices}

    return {}
