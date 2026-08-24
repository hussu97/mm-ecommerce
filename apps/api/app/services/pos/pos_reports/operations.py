"""The sales forecast."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.order import Order
from app.services.pos import business_day_service

from ._base import (
    CLOSED,
    ZERO,
)


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
