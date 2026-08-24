"""Menu engineering, speed of service, branch trends, tables and the forecast."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.branch import Branch
from app.models.order import Order
from app.models.pos_order import (
    KitchenTicket,
)
from app.models.pos_table import PosTable, Section
from app.models.product import Product
from app.services.pos import business_day_service

from ._base import (
    CLOSED,
    ZERO,
    _scope,
)
from .sales import _sales_by_item


async def menu_engineering(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Classify menu items by popularity and margin — the classic
    star / plough-horse / puzzle / dog quadrants.
    """
    rows = await _sales_by_item(
        db,
        group_by_category=False,
        branch_id=branch_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    if not rows:
        return []

    total_quantity = sum(r["quantity"] for r in rows)
    average_quantity = total_quantity / len(rows) if rows else 0

    enriched: list[dict] = []
    for row in rows:
        product = (
            await db.get(Product, uuid.UUID(row["key"]))
            if row["key"] != "unknown"
            else None
        )
        cost = Decimal(str(product.cost)) if product and product.cost else ZERO
        revenue = row["net_sales"]
        item_cost = money(cost * row["quantity"])
        margin = money(revenue - item_cost)
        margin_percent = float(margin / revenue) if revenue else 0.0
        enriched.append(
            {
                **row,
                "cost": item_cost,
                "margin": margin,
                "margin_percent": margin_percent,
            }
        )

    average_margin = (
        sum(r["margin_percent"] for r in enriched) / len(enriched) if enriched else 0
    )

    for row in enriched:
        popular = row["quantity"] >= average_quantity
        profitable = row["margin_percent"] >= average_margin
        row["classification"] = (
            "star"
            if popular and profitable
            else "plough_horse"
            if popular
            else "puzzle"
            if profitable
            else "dog"
        )
    return enriched


async def speed_of_service(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """
    How long the kitchen and the counter actually take.

    Three spans, each measured only over the tickets that reached that stage,
    so a ticket still cooking does not drag the "prep" average down:

      * acknowledge — sent to the kitchen until someone started it
      * prep        — started until marked ready
      * total       — sent until ready, the number a customer feels

    Averages alone hide the bad days, so the slowest ticket is reported too.

    **This report has no data source today and returns empty spans.** Both
    `started_at` and `completed_at` were only ever written by the KDS
    ticket-status endpoint, and there has never been a kitchen display screen to
    call it — the tickets are printed, not bumped. The query is left in place
    because it is correct and becomes useful the moment something acknowledges a
    ticket; it is documented here so an empty card is not read as "the kitchen
    took no time".
    """
    started = KitchenTicket.started_at.isnot(None)
    completed = KitchenTicket.completed_at.isnot(None)

    def seconds(start, end):
        return func.extract("epoch", end - start)

    stmt = (
        select(
            func.count(KitchenTicket.id),
            func.count(KitchenTicket.id).filter(started),
            func.count(KitchenTicket.id).filter(completed),
            func.avg(seconds(KitchenTicket.sent_at, KitchenTicket.started_at)).filter(
                started
            ),
            func.avg(
                seconds(KitchenTicket.started_at, KitchenTicket.completed_at)
            ).filter(completed & started),
            func.avg(seconds(KitchenTicket.sent_at, KitchenTicket.completed_at)).filter(
                completed
            ),
            func.max(seconds(KitchenTicket.sent_at, KitchenTicket.completed_at)).filter(
                completed
            ),
        )
        .select_from(KitchenTicket)
        .join(Order, Order.id == KitchenTicket.order_id)
        .where(KitchenTicket.sent_at.isnot(None))
    )
    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)

    (
        tickets,
        acknowledged,
        finished,
        avg_ack,
        avg_prep,
        avg_total,
        slowest,
    ) = (await db.execute(stmt)).one()

    def minutes(value) -> Decimal:
        return money(Decimal(str(value or 0)) / Decimal(60))

    return {
        "tickets": int(tickets or 0),
        "acknowledged": int(acknowledged or 0),
        "completed": int(finished or 0),
        # Still open at the end of the window — the queue, not a delay.
        "outstanding": int((tickets or 0) - (finished or 0)),
        "avg_acknowledge_minutes": minutes(avg_ack),
        "avg_prep_minutes": minutes(avg_prep),
        "avg_total_minutes": minutes(avg_total),
        "slowest_ticket_minutes": minutes(slowest),
    }


async def branches_trend(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    Each branch's sales per business day, for comparing sites over time.

    Returned long rather than pivoted: the caller decides whether to draw one
    line per branch or a table, and a pivot would have to guess the date range
    that happens to have data.
    """
    stmt = (
        _scope(
            select(
                Branch.name,
                Order.business_date,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(Branch, Branch.id == Order.branch_id)
        .where(Order.pos_status == CLOSED)
        .group_by(Branch.name, Order.business_date)
        .order_by(Order.business_date.asc(), Branch.name.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "branch": name,
            "business_date": day,
            "orders": int(count or 0),
            "net_sales": money(total),
            "average_order_value": money(Decimal(str(total or 0)) / count)
            if count
            else ZERO,
        }
        for name, day, count, total in rows
    ]


async def table_utilization(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    How hard each table works: covers, turns and how long a party sits.

    Only dine-in checks count. A takeaway order has no table, and counting it
    would flatter every number here.
    """
    minutes = func.extract("epoch", Order.closed_at - Order.opened_at) / 60

    stmt = (
        _scope(
            select(
                Section.name,
                PosTable.name,
                PosTable.seats,
                func.count(Order.id),
                func.coalesce(func.sum(Order.guests), 0),
                func.coalesce(func.sum(Order.total), 0),
                func.avg(minutes),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(PosTable, PosTable.id == Order.table_id)
        .outerjoin(Section, Section.id == PosTable.section_id)
        .where(Order.pos_status == CLOSED, Order.closed_at.isnot(None))
        .group_by(Section.name, PosTable.name, PosTable.seats)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "section": section or "Unassigned",
            "table": table,
            "seats": int(seats or 0),
            # "Turns" is covers per seat: how many parties that seat served.
            "turns": int(turns or 0),
            "covers": int(covers or 0),
            "net_sales": money(total),
            "average_minutes": money(Decimal(str(avg_minutes or 0))),
            "sales_per_seat": money(Decimal(str(total or 0)) / seats)
            if seats
            else ZERO,
        }
        for section, table, seats, turns, covers, total, avg_minutes in rows
    ]


async def sales_predictions(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    days_ahead: int = 7,
    lookback_weeks: int = 8,
) -> list[dict]:
    """
    Forecast the next few trading days from the same weekday's history.

    Deliberately a weekday average rather than a trend line or a model. A
    bakery's week is dominated by day-of-week shape — Saturday is not a
    slightly larger Tuesday — and a linear trend over mixed weekdays mostly
    predicts which days happened to fall in the window. Averaging each
    weekday against itself captures the pattern that actually exists.

    Every row carries the sample it was computed from, because a forecast
    from two Saturdays is a guess and the reader should be able to see that.
    """
    day_of_week = func.extract("dow", func.cast(Order.business_date, sa.Date))

    stmt = (
        select(
            day_of_week.label("dow"),
            func.count(func.distinct(Order.business_date)),
            func.avg(Order.total),
            func.sum(Order.total),
            func.count(Order.id),
        )
        .select_from(Order)
        .where(Order.pos_status == CLOSED)
        .group_by(day_of_week)
    )
    if branch_id:
        stmt = stmt.where(Order.branch_id == branch_id)

    rows = {int(r[0]): r for r in (await db.execute(stmt)).all() if r[0] is not None}
    if not rows:
        return []

    # Daily totals, not per-order averages: what a manager rosters against is
    # "how much will Saturday take", not "what will each customer spend".
    daily = {}
    for dow, days, _avg_order, total, orders in rows.values():
        day_count = int(days or 0) or 1
        daily[dow] = {
            "days_observed": int(days or 0),
            "avg_daily_sales": money(Decimal(str(total or 0)) / day_count),
            "avg_daily_orders": int((orders or 0) / day_count),
        }

    today = business_day_service.shop_today()
    predictions = []
    for offset in range(1, days_ahead + 1):
        target = today + timedelta(days=offset)
        # Postgres extract(dow) is 0=Sunday; Python weekday() is 0=Monday.
        dow = (target.weekday() + 1) % 7
        seen = daily.get(dow)
        predictions.append(
            {
                "business_date": target.isoformat(),
                "weekday": target.strftime("%A"),
                "predicted_sales": seen["avg_daily_sales"] if seen else ZERO,
                "predicted_orders": seen["avg_daily_orders"] if seen else 0,
                # Named plainly so nobody mistakes one Saturday for a trend.
                "based_on_days": seen["days_observed"] if seen else 0,
                "confidence": (
                    "none"
                    if not seen
                    else "low"
                    if seen["days_observed"] < 3
                    else "medium"
                    if seen["days_observed"] < lookback_weeks
                    else "high"
                ),
            }
        )
    return predictions
