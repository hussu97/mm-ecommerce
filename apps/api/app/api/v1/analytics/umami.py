"""
Traffic and funnel, proxied from Umami Cloud.

The only part of the analytics screen whose numbers are not ours. It shares
nothing with `commerce.py` beyond the date window: different source, different
failure mode — Umami being down is a degraded panel, not a broken dashboard —
and its own HTTP client and timeouts.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set
from app.core.config import settings
from app.core.deps import get_db
from app.core.permissions import require
from app.models.order import Order, OrderStatusEnum
from app.models.user import User

from ._shared import _ANALYTICS_TTL, _date_range, _to_ms, logger
from .schemas import EventCount, FunnelData, PageviewPoint, TopPage, TrafficData

router = APIRouter()


@router.get("/funnel", response_model=FunnelData)
async def get_funnel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """Order counts by status (funnel)."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:funnel:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return FunnelData(**cached)

    stmt = (
        select(Order.status, func.count(Order.id).label("count"))
        .where(
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(Order.status)
    )
    rows = (await db.execute(stmt)).all()
    counts: dict[OrderStatusEnum, int] = {row.status: int(row.count) for row in rows}

    created = counts.get(OrderStatusEnum.CREATED, 0)
    # `arrived_at_pos` counts as confirmed here. The bucket means "paid for and
    # not yet finished", and an order waiting for its run is exactly that —
    # splitting it out would put a step in a funnel about *checkout* that is
    # really about our dispatch schedule. Folding it in also keeps `total`
    # whole: left out, every batched order would vanish from the denominator
    # and quietly flatter the conversion rate.
    confirmed = counts.get(OrderStatusEnum.CONFIRMED, 0) + counts.get(
        OrderStatusEnum.ARRIVED_AT_POS, 0
    )
    packed = counts.get(OrderStatusEnum.PACKED, 0)
    cancelled = counts.get(OrderStatusEnum.CANCELLED, 0)
    total = created + confirmed + packed + cancelled

    conversion_rate = round(packed / total * 100, 1) if total else 0.0

    result_obj = FunnelData(
        created=created,
        confirmed=confirmed,
        packed=packed,
        cancelled=cancelled,
        conversion_rate=conversion_rate,
    )
    await cache_set(cache_key, result_obj.model_dump(mode="json"), ttl=_ANALYTICS_TTL)
    return result_obj


@router.get("/traffic", response_model=TrafficData)
async def get_traffic(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    _admin: User = Depends(require("reports.sales")),
):
    """
    Traffic and custom events, read back from Umami Cloud.

    Every failure here used to return zeros. A revoked key, a plan whose API
    access has lapsed, a website ID pointing at a site nobody visits and a genuinely
    quiet Tuesday all rendered as the same four noughts on the dashboard, and the
    only way to tell them apart was to read the API's reply by hand from the VM.
    They are told apart now: `error` carries the reason when the reason is ours.
    """

    def _empty(*, configured: bool, error: str | None = None) -> TrafficData:
        return TrafficData(
            visitors=0,
            sessions=0,
            pageviews=0,
            bounce_rate=0.0,
            avg_duration=0.0,
            pageviews_chart=[],
            top_pages=[],
            events=[],
            configured=configured,
            error=error,
        )

    if not settings.UMAMI_API_KEY or not settings.UMAMI_WEBSITE_ID:
        return _empty(configured=False)

    start, end = _date_range(start_date, end_date)
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) + 86_399_999  # inclusive end-of-day

    base = f"https://api.umami.is/v1/websites/{settings.UMAMI_WEBSITE_ID}"
    headers = {"x-umami-api-key": settings.UMAMI_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            responses = await _umami_fetch_all(client, base, headers, start_ms, end_ms)
    except Exception as exc:
        logger.warning("Umami read failed: %s", exc)
        return _empty(configured=True, error=f"Could not reach Umami: {exc}")

    failure = _umami_failure(responses)
    if failure:
        logger.warning("Umami read rejected: %s", failure)
        return _empty(configured=True, error=failure)

    stats_resp, pv_resp, pages_resp, events_resp = (
        _umami_json(responses[0], {}),
        _umami_json(responses[1], {}),
        _umami_json(responses[2], []),
        _umami_json(responses[3], []),
    )

    try:
        # Umami v2 returns flat integers: {"visitors": 1, "pageviews": 5, ...}
        # with an optional nested "comparison" key
        def _stat(key: str) -> float:
            v = stats_resp.get(key, 0)
            # older Umami versions returned {"value": N}
            if isinstance(v, dict):
                return float(v.get("value", 0))
            return float(v)

        visitors = int(_stat("visitors"))
        sessions = int(_stat("visits"))  # Umami uses "visits" not "sessions"
        pageviews = int(_stat("pageviews"))

        # Umami reports `bounces` as a count of single-page visits and
        # `totaltime` as a sum of seconds. The dashboard prints one as a
        # percentage and the other as an average, so the division belongs here
        # — without it a good week read as a 300% bounce rate.
        bounce_rate = (_stat("bounces") / sessions * 100) if sessions else 0.0
        avg_duration = (_stat("totaltime") / sessions) if sessions else 0.0

        chart_items = pv_resp.get("pageviews", [])
        chart = [
            PageviewPoint(date=item.get("x", ""), views=int(item.get("y", 0)))
            for item in chart_items
        ]

        top = [
            TopPage(path=item.get("x", ""), views=int(item.get("y", 0)))
            for item in pages_resp[:10]
        ]

        events = sorted(
            (
                EventCount(name=item.get("x", ""), count=int(item.get("y", 0)))
                for item in events_resp
                if item.get("x")
            ),
            key=lambda e: e.count,
            reverse=True,
        )

        return TrafficData(
            visitors=visitors,
            sessions=sessions,
            pageviews=pageviews,
            bounce_rate=round(bounce_rate, 1),
            avg_duration=round(avg_duration, 0),
            pageviews_chart=chart,
            top_pages=top,
            events=events,
            configured=True,
        )
    except Exception as exc:
        logger.warning("Umami reply was not the shape we expected: %s", exc)
        return _empty(configured=True, error=f"Unreadable reply from Umami: {exc}")


async def _umami_fetch_all(
    client: httpx.AsyncClient, base: str, headers: dict, start_ms: int, end_ms: int
) -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
    """
    Stats, the pageview series, top paths, and the custom events.

    The responses come back whole rather than pre-parsed, because whether Umami
    said yes is as much of the answer as what it said.
    """
    import asyncio

    params = {"startAt": start_ms, "endAt": end_ms}

    async def fetch(url: str, extra: dict | None = None) -> httpx.Response:
        p = {**params, **(extra or {})}
        return await client.get(url, headers=headers, params=p)

    return await asyncio.gather(  # type: ignore[return-value]
        fetch(f"{base}/stats"),
        fetch(f"{base}/pageviews", {"unit": "day", "timezone": "Asia/Dubai"}),
        fetch(f"{base}/metrics", {"type": "path"}),
        # Every name `apps/web/lib/analytics.ts` tracks — add_to_cart,
        # begin_checkout, order_completed and the rest. Nothing read these
        # before, so the dashboard could not have shown a checkout event
        # arriving even when one did.
        fetch(f"{base}/metrics", {"type": "event"}),
    )


def _umami_failure(responses: tuple[httpx.Response, ...]) -> str | None:
    """The first refusal in a batch, phrased for somebody who has to fix it."""
    for resp in responses:
        if resp.is_success:
            continue
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                detail = (
                    err.get("message", "") if isinstance(err, dict) else str(err or "")
                )
        except Exception:
            detail = resp.text[:120]
        if resp.status_code in (401, 403):
            return (
                f"Umami refused the API key ({resp.status_code}"
                f"{': ' + detail if detail else ''}). Check UMAMI_API_KEY, and that "
                "the Umami Cloud plan on this account includes API access."
            )
        return f"Umami returned {resp.status_code}{': ' + detail if detail else ''}"
    return None


def _umami_json(resp: httpx.Response, fallback):
    try:
        return resp.json()
    except Exception:
        return fallback
