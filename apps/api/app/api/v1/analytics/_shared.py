"""
What every analytics module needs: the date window, the cache policy, and the
Umami timestamp format.

Kept apart from the endpoints because the three groups that import it —
`commerce`, `umami` and `live_carts` — answer unrelated questions and share
nothing else.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from app.services.pos import business_day_service

logger = logging.getLogger(__name__)

#: How stale the console's figures may be, and the only thing that makes them
#: fresh again.
#:
#: Five minutes, expiring on its own — there is deliberately no invalidation.
#: Five things move these numbers (a checkout, a payment webhook confirming
#: one, an admin changing a status, a refund, and every sale at a till), and
#: for a while exactly one of them dropped these keys, which is worse than none
#: dropping them: it read as a freshness guarantee that four writers out of
#: five quietly broke. Busting on all five would mean a Redis keyspace scan on
#: every counter sale to save a margin dashboard five minutes, which is not a
#: trade worth making. So: one rule, same for every writer, stated here.
_ANALYTICS_TTL = 300


def _date_range(
    start_date: Optional[date],
    end_date: Optional[date],
) -> tuple[date, date]:
    if not end_date:
        end_date = business_day_service.shop_today()
    if not start_date:
        start_date = end_date - timedelta(days=30)
    return start_date, end_date


def _to_ms(d: date) -> int:
    """Convert date to milliseconds timestamp (Umami API format)."""
    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)
