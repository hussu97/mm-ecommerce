"""Keeta's in-page order pull — the one fetch that must run inside the page.

Keeta (Meituan's infra) signs every XHR with `mtgsig`, a per-request signature
its own obfuscated JS computes and stamps on the request. There is no token to
lift and replay, so the httpx sweep that serves every other channel cannot serve
Keeta (see the mm-ecommerce `keeta_provider` docstring). Instead the bootstrap
worker holds the live browser session and evaluates the `getOrders` fetch *in the
page*, where the portal's JS signs it, and hands the raw JSON back out.

`fetch_keeta_orders` opens the order-history page from the stored session, reads
`SHOP_IDS` from `sessionStorage`, and for each shop and each calendar-month
window POSTs `/api/order/history/getOrders` via `page.evaluate` (so the fetch is
the page's own, signed), paginating until the window is exhausted. It returns the
raw `getOrders` response payloads unchanged — the exact shape the mm-ecommerce
`keeta_provider.parse_orders` walks (`data.list[]` of
`baseOrder`/`merchantOrder`/`products`/`feeDtl` envelopes).

Ported from mm-aggregator-automation `channels/keeta/exports.py`
(`_fetch_keeta_history_rows` / `_request_keeta_browser_json`). Playwright is
imported nowhere here — the function is handed an already-open context, so this
module (and its tests) import without the browser library.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: The shop's clock. Keeta's history filter takes epoch-millis bounds, and the
#: business day they mean is Dubai wall-clock — the same zone the parser emits.
_BUSINESS_TZ = ZoneInfo("Asia/Dubai")

#: The signed history endpoint (relative, so the page's own origin + cookies +
#: mtgsig apply when the in-page fetch resolves it).
KEETA_ORDER_HISTORY_ENDPOINT = "/api/order/history/getOrders"
#: The page that boots the order-history SPA; navigating here puts the session on
#: merchant.mykeeta.com and primes `SHOP_IDS` in sessionStorage before we fetch.
KEETA_ORDER_HISTORY_ROUTE = (
    "https://merchant.mykeeta.com/order-manager/m/web/mach/"
    "b_pc_order_history_list?containerType=orderManager"
)

#: Keeta's history page size. Pagination stops once page*size covers totalCount.
_PAGE_SIZE = 30

# The in-page reader for the shop ids the portal stashes in sessionStorage.
_SHOP_IDS_JS = """
() => {
  const value = sessionStorage.getItem("SHOP_IDS");
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch (error) {
    return String(value).split(",").map((item) => item.trim()).filter(Boolean);
  }
}
"""

# The in-page fetch: the page's own `fetch`, so Meituan's JS signs it (mtgsig).
# Headers come from sessionStorage, exactly as the portal sends them.
_GET_ORDERS_JS = """
async ({ endpoint, payload }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": "0",
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const response = await fetch(endpoint, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(payload || {})
  });
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch (error) {
    return { status: response.status, text };
  }
}
"""


def _add_months(value: date, months: int) -> date:
    """`value` shifted by `months` (can be negative), landing on the 1st."""
    total = (value.year * 12 + (value.month - 1)) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def _month_windows(months_back: int) -> list[tuple[date, date]]:
    """Calendar-month (start, end) windows from `months_back` months ago to today.

    `months_back=1` yields last month's full window and the current month up to
    today; `months_back=0` is just the current month. Each window is clamped to
    the requested span so the first and last are partial where they should be.
    """
    today = datetime.now(_BUSINESS_TZ).date()
    from_date = _add_months(date(today.year, today.month, 1), -max(months_back, 0))
    windows: list[tuple[date, date]] = []
    current = date(from_date.year, from_date.month, 1)
    while current <= today:
        next_month = _add_months(current, 1)
        last_day = date.fromordinal(next_month.toordinal() - 1)
        windows.append((max(from_date, current), min(today, last_day)))
        current = next_month
    return windows


def _date_ms(value: date, *, end_of_day: bool = False) -> str:
    """A Dubai-local calendar date as an epoch-millis string, as Keeta expects."""
    clock = time(23, 59, 59, 999_000) if end_of_day else time(0, 0, 0)
    dt = datetime.combine(value, clock, tzinfo=_BUSINESS_TZ)
    return str(int(dt.timestamp() * 1000))


async def _read_shop_ids(page: Any) -> list[str]:
    """The shop ids the portal put in sessionStorage, or [] if none/unreadable."""
    try:
        result = await page.evaluate(_SHOP_IDS_JS)
    except Exception:  # noqa: BLE001 — a missing key must not abort the pull
        logger.warning("keeta: could not read SHOP_IDS from sessionStorage")
        return []
    if isinstance(result, list):
        return [str(shop_id) for shop_id in result if shop_id]
    return []


async def _get_orders_page(
    page: Any,
    *,
    shop_ids: list[str],
    window_start: date,
    window_end: date,
    page_number: int,
) -> Any:
    """One in-page `getOrders` POST — the raw JSON the page's signed fetch returns."""
    payload = {
        "startTime": _date_ms(window_start),
        "endTime": _date_ms(window_end, end_of_day=True),
        "orderType": 0,
        "pageNum": page_number,
        "pageSize": _PAGE_SIZE,
        "seqNoStr": "",
        "shopIds": shop_ids,
        "merchantOpType": 29,
    }
    return await page.evaluate(
        _GET_ORDERS_JS,
        {"endpoint": KEETA_ORDER_HISTORY_ENDPOINT, "payload": payload},
    )


async def fetch_keeta_orders(context: Any, *, months_back: int = 1) -> list[dict]:
    """Pull raw Keeta `getOrders` payloads in-page from an open browser context.

    Opens the order-history page (so the session sits on merchant.mykeeta.com and
    `SHOP_IDS` is primed), then for each shop and each month window in the lookback
    calls the signed `getOrders` fetch through `page.evaluate`, paginating until
    the window is exhausted. Returns the raw response payloads unchanged — one dict
    per fetched page — for `keeta_provider.parse_orders` to walk downstream.
    """
    page = await context.new_page()
    payloads: list[dict] = []
    try:
        await page.goto(
            KEETA_ORDER_HISTORY_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        # Give the SPA a beat to boot and populate SHOP_IDS.
        await page.wait_for_timeout(6_000)

        shop_ids = await _read_shop_ids(page)
        # Iterate per shop (single-element shopIds list) so each shop paginates on
        # its own totals; if none are known, make one combined call with [].
        shop_groups = [[shop_id] for shop_id in shop_ids] or [[]]
        windows = _month_windows(months_back)

        for shop_group in shop_groups:
            for window_start, window_end in windows:
                page_number = 1
                while True:
                    response_payload = await _get_orders_page(
                        page,
                        shop_ids=shop_group,
                        window_start=window_start,
                        window_end=window_end,
                        page_number=page_number,
                    )
                    if not isinstance(response_payload, dict):
                        break
                    payloads.append(response_payload)

                    data = response_payload.get("data")
                    rows = data.get("list") if isinstance(data, dict) else None
                    if not isinstance(rows, list) or not rows:
                        break
                    total_count = int(
                        data.get("totalCount") or data.get("total") or len(rows)
                    )
                    if page_number * _PAGE_SIZE >= total_count:
                        break
                    page_number += 1
        return payloads
    finally:
        await page.close()
