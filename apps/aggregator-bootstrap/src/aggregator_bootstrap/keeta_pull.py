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

`fetch_keeta_finance` pulls the real finance files: it signs the two LIST calls
in-page (`statementfile/list` monthly commission invoices, `download/task/list`
weekly billing reports), then downloads each returned **presigned S3 URL** with
plain httpx (no browser, no mtgsig — the URL is self-authorising) and base64s the
bytes into finance payloads the mm-ecommerce `keeta_provider.parse_finance`
turns into statements, per-order lines (from the XLSX) and an archived invoice
(from the zip). A truncation note is returned when a download failed or the size
guard tripped. Do NOT attempt PDF OCR here.

Ported from mm-aggregator-automation `channels/keeta/exports.py`
(`_fetch_keeta_history_rows` / `_request_keeta_browser_json`). Playwright is
imported nowhere here — the function is handed an already-open context, so this
module (and its tests) import without the browser library. `evaluate_in_page`
runs the fetch in the page's main world so `mtgsig` and sessionStorage are
visible.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

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
#: Hard ceiling on order pages per (shop, window) so an absent/blocked
#: `totalCount` — an API bug, or a session that has quietly expired — cannot spin
#: the in-page fetch forever. Mirrors the finance byte guard below and the
#: api-side talabat `_MAX_FINANCE_PAGES`.
_MAX_ORDER_PAGES = 200

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

#: The signed-in account id the getOrders fetch stamps on every request. Empty
#: means the in-page session is signed out — the fetch would still fire with a
#: blank `accountid` and silently get risk-controlled JSON back, which reads like
#: "no orders" rather than "logged out". We assert it before pulling.
_LOGIN_ACCOUNTID_JS = '() => sessionStorage.getItem("LOGIN_ACCOUNTID") || ""'

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

        # Assert the session is actually signed in before pulling. A blank
        # LOGIN_ACCOUNTID means the hydrated session is logged out; getOrders
        # would still fire and come back risk-controlled, which parses as "no
        # orders" and silently truncates the day. Fail loud instead so the warm
        # surfaces it as needing a headed re-login.
        account_id = ""
        try:
            account_id = str(
                await evaluate_in_page(page, _LOGIN_ACCOUNTID_JS) or ""
            ).strip()
        except Exception:  # noqa: BLE001 — treat an unreadable value as absent
            account_id = ""
        if not account_id:
            from .browser import NeedsHumanLogin

            raise NeedsHumanLogin(
                "keeta LOGIN_ACCOUNTID is empty — the in-page session is signed "
                "out; getOrders would return risk-controlled data. "
                "Run: aggregator-bootstrap login --channel keeta"
            )

        shop_ids = await _read_shop_ids(page)
        # Iterate per shop (single-element shopIds list) so each shop paginates on
        # its own totals; if none are known, make one combined call with [].
        shop_groups = [[shop_id] for shop_id in shop_ids] or [[]]
        windows = _month_windows(months_back)

        for shop_group in shop_groups:
            for window_start, window_end in windows:
                page_number = 1
                for _ in range(_MAX_ORDER_PAGES):
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
                else:
                    # Cap reached without a natural stop — a `totalCount` that
                    # never lets the loop finish. Stop rather than fetch forever;
                    # the pages gathered so far are still returned.
                    logger.warning(
                        "keeta: order pagination hit the %d-page cap for shop %s "
                        "window %s–%s; results may be truncated",
                        _MAX_ORDER_PAGES,
                        shop_group or "[]",
                        window_start,
                        window_end,
                    )
        return payloads
    finally:
        await page.close()


# ── finance (real LIST endpoints + presigned-S3 download) ───────────────────
# Keeta's finance figures are NOT in a JSON bill list — the merchant portal only
# exposes them as downloadable files: a monthly VAT commission-invoice PDF (in a
# zip) and a per-shop weekly billing-report XLSX. The three LIST endpoints below
# are signed like every other Keeta XHR, so they run in-page (`evaluate_in_page`)
# where the portal's JS stamps `mtgsig`. But every `downloadUrl` they return is a
# **presigned S3 URL** (AWSAccessKeyId/Signature/Expires, expiry ~2046) — those
# download by plain httpx from anywhere, no browser and no signing needed. So the
# flow is: sign the LIST call in-page → pull the file bytes with httpx → base64
# them into a finance payload the mm-ecommerce `keeta_provider.parse_finance`
# turns into statements + lines + an archived invoice.

#: The financial-center SPA page — navigating here primes the session on
#: merchant.mykeeta.com and populates SHOP_IDS in sessionStorage.
KEETA_FINANCE_ROUTE = (
    "https://merchant.mykeeta.com/financial-center/m/web/mach/"
    "b_pc_finance_statement?containerType=financialCenter"
)
#: Query suffix Keeta puts on every merchant XHR (platform/version tags).
KEETA_QUERY_SUFFIX = "?yodaReady=h5&csecplatform=4&csecversion=3.5.1"
#: Monthly commission-invoice list (account-level): data[].statementFileList[].
KEETA_STATEMENT_FILE_ENDPOINT = (
    "/api/settlement/statement/v2/r/statementfile/list" + KEETA_QUERY_SUFFIX
)
#: Per-shop weekly billing-task list: data.pageContent[] of download tasks.
KEETA_DOWNLOAD_TASK_ENDPOINT = (
    "/api/settlement/statement/v2/r/download/task/list" + KEETA_QUERY_SUFFIX
)
#: Shop-id lookup — the V2 form (the non-V2 returns "invalid param").
KEETA_SHOP_LIST_ENDPOINT = (
    "/api/account/query/getShopListByAccountV2" + KEETA_QUERY_SUFFIX
)

#: downloadTaskType 3 = order billing report (the weekly XLSX).
_DOWNLOAD_TASK_TYPE_BILLING = 3
#: taskStatus 30 = ready/completed (a signed downloadUrl is present).
_TASK_STATUS_READY = 30
#: Last-resort shopIds for this account (customerId 330066) — used only when the
#: V2 endpoint, sessionStorage, AND the account's own `extras["shop_ids"]` all
#: come up empty. The account row is the preferred source (threaded in as
#: `fallback_shop_ids` by the worker); this hard-coded tuple stays as the final
#: fallback so a fresh account with no extras behaves exactly as before.
_KNOWN_SHOP_IDS = ("1644336388", "1644174206", "1644170195", "1644189187")

#: Guard the total downloaded finance bytes so one runaway month cannot balloon
#: the push. base64 inflates ~4/3, so this caps the pushed payload near ~64 MB.
_MAX_FINANCE_DOWNLOAD_BYTES = 48 * 1024 * 1024

# A generic in-page signed POST — the page's own `fetch`, so Meituan's JS signs
# it (mtgsig). Used for the three finance LIST calls.
_POST_JSON_JS = """
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

#: The shopId is embedded in a task's name/type as `[1644189187]`.
_SHOP_ID_IN_TEXT = re.compile(r"\[(\d{6,})\]")


async def _post_in_page(page: Any, endpoint: str, payload: dict) -> Any:
    """One signed in-page POST — the raw JSON the page's own fetch returns."""
    return await evaluate_in_page(
        page, _POST_JSON_JS, {"endpoint": endpoint, "payload": payload}
    )


async def _resolve_shop_ids(
    page: Any,
    *,
    fallback_shop_ids: list[str] | None = None,
    customer_id: str | None = None,
) -> list[str]:
    """Shop ids from the V2 endpoint, else sessionStorage, else the account.

    Resolution order: the live `getShopListByAccountV2` response, then the
    portal's sessionStorage, then `fallback_shop_ids` (the account's
    `extras["shop_ids"]`, threaded in by the worker), then the hard-coded
    `_KNOWN_SHOP_IDS` as the last resort. `customer_id` (from
    `extras["customer_id"]`) is sent in the V2 body when present, which helps
    the endpoint scope to this account; when absent the body is empty, exactly
    as before — so behaviour is unchanged until an operator populates extras.
    """
    body = {"customerId": customer_id} if customer_id else {}
    try:
        response = await _post_in_page(page, KEETA_SHOP_LIST_ENDPOINT, body)
    except Exception:  # noqa: BLE001 — fall back rather than abort the finance pull
        logger.warning("keeta finance: getShopListByAccountV2 failed")
        response = None
    ids: list[str] = []
    if isinstance(response, dict):
        for candidate in _iter_dicts(response.get("data")):
            for key in ("shopId", "shop_id", "id", "poiId"):
                value = candidate.get(key)
                if value not in (None, "", 0):
                    ids.append(str(value))
                    break
    # De-dup, keep order; only digit-like ids (guards against picking noise).
    seen: set[str] = set()
    ids = [i for i in ids if i.isdigit() and not (i in seen or seen.add(i))]
    if ids:
        return ids
    from_session = await _read_shop_ids(page)
    # The account's own extras win over the hard-coded constant.
    return from_session or list(fallback_shop_ids or _KNOWN_SHOP_IDS)


def _iter_dicts(value: Any) -> list[dict]:
    """Every dict in a nested list/dict tree — the V2 shape varies by account."""
    out: list[dict] = []
    if isinstance(value, dict):
        out.append(value)
        for child in value.values():
            out.extend(_iter_dicts(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_iter_dicts(child))
    return out


def _shop_id_from_text(*texts: Any) -> str | None:
    for text in texts:
        if not text:
            continue
        match = _SHOP_ID_IN_TEXT.search(str(text))
        if match:
            return match.group(1)
    return None


async def _download_b64(url: str) -> tuple[str, int]:
    """Download a presigned S3 URL with plain httpx → (base64, byte length)."""
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = resp.content
    return base64.b64encode(body).decode("ascii"), len(body)


async def fetch_keeta_finance(
    context: Any,
    *,
    months_back: int = 2,
    fallback_shop_ids: list[str] | None = None,
    customer_id: str | None = None,
) -> tuple[list[dict], str | None]:
    """Pull Keeta finance files in-page + by presigned httpx, as push payloads.

    `fallback_shop_ids` and `customer_id` come from the Keeta account's
    `extras` (`extras["shop_ids"]` / `extras["customer_id"]`, threaded in by the
    worker). They feed `_resolve_shop_ids`: the account row is preferred over
    the hard-coded `_KNOWN_SHOP_IDS`, and both are null-safe — when absent the
    resolution is identical to before.

    Navigates to the financial-center SPA (priming the merchant session), signs
    the two LIST calls in-page, then downloads each returned presigned file with
    httpx (no browser needed) and base64-encodes it into a finance payload:

    - **Monthly commission invoice** — `statementfile/list` → each
      `statementFileList[]` entry's zip is carried as `invoice_zip_b64` with a
      `KEETA_COMMISSION_{time}` identity.
    - **Weekly billing report** — `download/task/list` (downloadTaskType 3) →
      each ready task's XLSX is carried as `bill_xlsx_b64`, keyed by its
      `taskViewId`, with the shopId lifted from the task name.

    Returns `(payloads, truncation_note)`; the note is set when a download failed
    or the size guard tripped. Each item is isolated — one bad file is logged and
    skipped, never aborting the rest.
    """
    page = await context.new_page()
    payloads: list[dict] = []
    notes: list[str] = []
    downloaded_bytes = 0

    def _budget_left() -> bool:
        return downloaded_bytes < _MAX_FINANCE_DOWNLOAD_BYTES

    try:
        await page.goto(
            KEETA_FINANCE_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(6_000)

        # ── monthly commission invoices (account-level) ───────────────────────
        try:
            monthly = await _post_in_page(
                page,
                KEETA_STATEMENT_FILE_ENDPOINT,
                {"pageNum": 1, "pageSize": max(months_back, 2) + 1},
            )
        except Exception:  # noqa: BLE001
            logger.exception("keeta finance: statementfile/list failed")
            monthly = None
            notes.append("statementfile/list fetch failed")

        for group in (
            (monthly or {}).get("data", []) if isinstance(monthly, dict) else []
        ):
            files = group.get("statementFileList") if isinstance(group, dict) else None
            for entry in files or []:
                if not isinstance(entry, dict):
                    continue
                url = entry.get("downloadUrl")
                time_val = entry.get("time") or (group.get("time"))
                if not url or time_val in (None, "", 0):
                    continue
                if not _budget_left():
                    notes.append("size budget reached before monthly invoices done")
                    break
                try:
                    b64, size = await _download_b64(str(url))
                except Exception:  # noqa: BLE001 — one bad file must not stop the rest
                    logger.warning("keeta finance: monthly invoice download failed")
                    notes.append(f"monthly invoice {time_val} download failed")
                    continue
                downloaded_bytes += size
                payloads.append(
                    {
                        "statement_id": f"KEETA_COMMISSION_{time_val}",
                        "time": time_val,
                        "fileScene": entry.get("fileScene") or "Commission invoice",
                        "createTimeText": entry.get("createTimeText"),
                        "invoice_zip_b64": b64,
                    }
                )

        # ── weekly billing reports (per shop, embedded in the task name) ──────
        shop_ids = await _resolve_shop_ids(
            page, fallback_shop_ids=fallback_shop_ids, customer_id=customer_id
        )
        # Bound the weekly-report re-download to a recent window. The list is
        # newest-first (pageNum=1), so a page sized to the lookback fetches the most
        # recent ~months_back of weekly reports instead of the entire history every
        # run (which re-downloaded dozens of settled XLSX each pull). ~5 weekly
        # reports/month + margin; ingest is idempotent, so the only cost of the old
        # behaviour was wasted bandwidth/time, but that time monopolised the daemon.
        weekly_page_size = max(max(months_back, 2) * 5, 10)
        try:
            tasks = await _post_in_page(
                page,
                KEETA_DOWNLOAD_TASK_ENDPOINT,
                {
                    "downloadTaskType": _DOWNLOAD_TASK_TYPE_BILLING,
                    "pageNum": 1,
                    "pageSize": weekly_page_size,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("keeta finance: download/task/list failed")
            tasks = None
            notes.append("download/task/list fetch failed")

        task_data = tasks.get("data") if isinstance(tasks, dict) else None
        page_content = (
            task_data.get("pageContent") if isinstance(task_data, dict) else None
        )
        for task in page_content or []:
            if not isinstance(task, dict):
                continue
            url = task.get("downloadUrl")
            if not url or task.get("taskStatus") != _TASK_STATUS_READY:
                continue
            task_view_id = str(
                task.get("taskViewId") or task.get("taskId") or ""
            ).strip()
            if not task_view_id:
                continue
            shop_id = _shop_id_from_text(
                task.get("taskName"), task.get("displayTypeText")
            )
            if shop_id is None and len(shop_ids) == 1:
                shop_id = shop_ids[0]
            if not _budget_left():
                notes.append("size budget reached before weekly reports done")
                break
            try:
                b64, size = await _download_b64(str(url))
            except Exception:  # noqa: BLE001
                logger.warning("keeta finance: weekly report download failed")
                notes.append(f"weekly report {task_view_id} download failed")
                continue
            downloaded_bytes += size
            payloads.append(
                {
                    "statement_id": task_view_id,
                    "taskViewId": task_view_id,
                    "shopId": shop_id,
                    "displayTimeText": task.get("displayTimeText"),
                    "fileScene": task.get("displayTypeText") or "Billing report",
                    "taskName": task.get("taskName"),
                    "bill_xlsx_b64": b64,
                }
            )

        if not payloads and not notes:
            notes.append(
                "Keeta finance LIST calls returned no ready files for the window."
            )
    except Exception:  # noqa: BLE001 — best-effort; a broken page must not stop orders
        logger.exception("keeta finance pull failed — skipped")
        notes.append("Keeta finance pull failed (see logs).")
    finally:
        await page.close()

    return payloads, ("; ".join(notes) if notes else None)
