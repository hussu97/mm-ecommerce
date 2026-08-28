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

`fetch_keeta_finance` is best-effort: it navigates to the financial-center SPA
and attempts to fetch the bill list in-page. Keeta's finance surface currently
exposes settled figures inside commission-invoice PDFs, so most responses will be
download-task metadata only; when no structured data is found the function returns
an empty list with a truncation note explaining why. Do NOT attempt PDF OCR here.

Ported from mm-aggregator-automation `channels/keeta/exports.py`
(`_fetch_keeta_history_rows` / `_request_keeta_browser_json`). Playwright is
imported nowhere here — the function is handed an already-open context, so this
module (and its tests) import without the browser library. `evaluate_in_page`
runs the fetch in the page's main world so `mtgsig` and sessionStorage are
visible.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .engine import evaluate_in_page

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
        result = await evaluate_in_page(page, _SHOP_IDS_JS)
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
    return await evaluate_in_page(
        page,
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


# ── finance (best-effort in-page pull) ──────────────────────────────────────
#: The financial-center SPA page — navigating here primes the session on
#: merchant.mykeeta.com and populates SHOP_IDS in sessionStorage.
KEETA_FINANCE_ROUTE = (
    "https://merchant.mykeeta.com/financial-center/m/web/mach/"
    "b_pc_finance_statement?containerType=financialCenter"
)
#: Keeta's bill-list endpoint (relative — the in-page fetch signs it with mtgsig).
KEETA_FINANCE_ENDPOINT = "/api/finance/bill/getBillList"

_GET_FINANCE_JS = """
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
  } catch (e) {
    return { status: response.status, text };
  }
}
"""

#: Keeta finance page size (bills per page).
_FINANCE_PAGE_SIZE = 20

#: Explain once, reference everywhere — the PDF constraint is not a code gap.
_PDF_TRUNCATION_NOTE = (
    "Keeta finance figures live in commission-invoice PDFs behind each "
    "download-task URL; the in-page worker can extract only the task "
    "metadata. Resolve the PDF invoices to obtain settled amounts."
)


async def fetch_keeta_finance(
    context: Any, *, months_back: int = 2
) -> tuple[list[dict], str | None]:
    """Best-effort pull of Keeta finance data in-page from an open browser context.

    Navigates to the financial-center SPA, then for each shop and each
    calendar-month window attempts the signed `getBillList` fetch. Returns
    `(payloads, truncation_note)`.

    The truncation_note is set (and payloads may be empty) when:
    - The endpoint returns no structured bill rows (only task/download metadata).
    - The fetch throws entirely (network, session, etc.).

    In either case the caller should push whatever payloads were collected (for
    `parse_finance` to walk) and log the note. Do NOT attempt PDF OCR here.
    """
    page = await context.new_page()
    payloads: list[dict] = []
    truncation_note: str | None = None
    try:
        await page.goto(
            KEETA_FINANCE_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(6_000)

        shop_ids = await _read_shop_ids(page)
        shop_groups = [[shop_id] for shop_id in shop_ids] or [[]]
        windows = _month_windows(months_back)

        for shop_group in shop_groups:
            for window_start, window_end in windows:
                payload = {
                    "startTime": _date_ms(window_start),
                    "endTime": _date_ms(window_end, end_of_day=True),
                    "pageNum": 1,
                    "pageSize": _FINANCE_PAGE_SIZE,
                    "shopIds": shop_group,
                }
                response = await evaluate_in_page(
                    page,
                    _GET_FINANCE_JS,
                    {"endpoint": KEETA_FINANCE_ENDPOINT, "payload": payload},
                )
                if isinstance(response, dict):
                    payloads.append(response)

        if not payloads:
            truncation_note = _PDF_TRUNCATION_NOTE
        else:
            # Check whether any payload carries settled rows or is task-only.
            has_rows = any(
                isinstance(r.get("data"), dict)
                and isinstance(r["data"].get("list"), list)
                and r["data"]["list"]
                for r in payloads
            )
            if not has_rows:
                truncation_note = _PDF_TRUNCATION_NOTE
    except Exception:  # noqa: BLE001 — best-effort; a broken page must not stop orders
        logger.exception("keeta finance in-page fetch failed — skipped")
        truncation_note = (
            "Keeta finance in-page fetch failed (see logs). "
            + _PDF_TRUNCATION_NOTE
        )
    finally:
        await page.close()

    return payloads, truncation_note
