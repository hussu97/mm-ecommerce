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


async def _reseed_keeta_storage(page: Any) -> None:
    """Rewrite extras after the SPA has booted on persistent keeta.chrome.

    Cookies survive a Chrome restart; sessionStorage does not. The SPA then
    clears an init-script seed during boot, which is why a pull after relogin
    saw empty LOGIN_ACCOUNTID. Reseed immediately before the signed fetch.
    """
    from .browser import restore_session_storage

    await restore_session_storage(page, "keeta")


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
    Returned newest-first so a budgeted pull captures last-2d before history.
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
    # Newest first so a 600s budget still captures last-2d before last-month
    # history. Oldest-first spent the whole KEETA_ORDERS budget on four shops
    # of prior-month pagination after LOGIN_ACCOUNTID was already restored.
    windows.reverse()
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
    `SHOP_IDS` is primed), then for each month window (newest first) and each shop
    calls the signed `getOrders` fetch through `page.evaluate`, paginating until
    the window is exhausted. Newest-first is load-bearing: the daemon's 600s
    budget otherwise spends itself on last-month history and last-2d stays 0.
    Returns the raw response payloads unchanged — one dict per fetched page —
    for `keeta_provider.parse_orders` to walk downstream.
    """
    page = await context.new_page()
    payloads: list[dict] = []
    try:
        await page.goto(
            KEETA_ORDER_HISTORY_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        # Give the SPA a beat to boot and populate SHOP_IDS.
        await page.wait_for_timeout(6_000)
        await _reseed_keeta_storage(page)

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
        # Windows are the outer loop (newest first) so every shop's current month
        # lands before any shop's last-month history — a 600s kill must not leave
        # last-2d at 0.
        shop_groups = [[shop_id] for shop_id in shop_ids] or [[]]
        windows = _month_windows(months_back)

        for window_start, window_end in windows:
            for shop_group in shop_groups:
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
# ── Menu (catalog sync) ──────────────────────────────────────────────────────
# The menu, like orders, is mtgsig-signed and must be read in-page. Endpoints +
# shapes verified live from the portal 2026-09-01 (merchant.mykeeta.com/m/web/product):
#   POST /api/sailorProduct/shopCategory/r/listShopCategory {shopId}
#       -> {code:0, data:[{id, name, status, availableTimeDTO:{values:[...]}}]}
#   POST /api/sailorProduct/spu/r/listSpu {shopId, pageNum, pageSize}
#       -> {code:0, data:{spuList:[{id, name, status, shopCategoryIdList:[catId],
#                        skuList:[{price, currency}]}]}}
# The API side (`menu_readers.parse_keeta_menu`) turns the pushed
# {shopId, categories, spus} into a NormalizedMenu.

#: The menu-manager SPA page — navigating here primes the session on
#: merchant.mykeeta.com and populates SHOP_IDS + LOGIN_ACCOUNTID in sessionStorage.
KEETA_MENU_ROUTE = "https://merchant.mykeeta.com/m/web/product"
KEETA_CATEGORY_ENDPOINT = "/api/sailorProduct/shopCategory/r/listShopCategory"
KEETA_SPU_ENDPOINT = "/api/sailorProduct/spu/r/listSpu"
#: Products per listSpu page; one big page covers a bakery menu (≈45 items).
_MENU_PAGE_SIZE = 500

# The in-page menu read: two of the page's own signed fetches (mtgsig). Headers
# mirror the getOrders fetch so the signed session is recognised.
_GET_MENU_JS = """
async ({ catEndpoint, spuEndpoint, shopId, pageSize }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": String(shopId),
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  async function post(endpoint, body) {
    const response = await fetch(endpoint, {
      method: "POST", credentials: "include", headers, body: JSON.stringify(body)
    });
    const text = await response.text();
    try { return JSON.parse(text); } catch (e) { return { status: response.status, text }; }
  }
  const cat = await post(catEndpoint, { shopId });
  const spu = await post(spuEndpoint, { shopId, pageNum: 1, pageSize });
  return {
    shopId: String(shopId),
    categories: (cat && cat.data) || [],
    spus: (spu && spu.data && spu.data.spuList) || []
  };
}
"""


async def fetch_keeta_menu(context: Any) -> list[dict]:
    """The Keeta menu for every shop, read in-page (mtgsig) — one payload per shop.

    Mirrors `fetch_keeta_orders`: open the menu page (primes SHOP_IDS +
    LOGIN_ACCOUNTID), assert the session is signed in, then run the two signed menu
    fetches per shop. Each payload is `{shopId, categories, spus}` for the API's
    `parse_keeta_menu`. Playwright imported lazily by the caller's context."""
    page = await context.new_page()
    payloads: list[dict] = []
    try:
        await page.goto(KEETA_MENU_ROUTE, wait_until="domcontentloaded", timeout=60_000)
        # Give the SPA a beat to boot and populate LOGIN_ACCOUNTID + SHOP_IDS —
        # exactly like fetch_keeta_orders. Without this the read fires before the
        # portal JS sets them and looks "signed out" (seen on the first live run).
        await page.wait_for_timeout(6_000)
        await _reseed_keeta_storage(page)
        account_id = await evaluate_in_page(page, _LOGIN_ACCOUNTID_JS)
        if not account_id:
            logger.error("keeta menu: in-page session signed out (no LOGIN_ACCOUNTID)")
            return []
        shop_ids = await _read_shop_ids(page)
        if not shop_ids:
            logger.warning("keeta menu: no SHOP_IDS in sessionStorage")
            return []
        for shop_id in shop_ids:
            try:
                result = await evaluate_in_page(
                    page,
                    _GET_MENU_JS,
                    {
                        "catEndpoint": KEETA_CATEGORY_ENDPOINT,
                        "spuEndpoint": KEETA_SPU_ENDPOINT,
                        "shopId": int(shop_id),
                        "pageSize": _MENU_PAGE_SIZE,
                    },
                )
            except Exception:  # noqa: BLE001 — one shop must not abort the rest
                logger.warning("keeta menu: fetch failed for shop %s", shop_id)
                continue
            if isinstance(result, dict) and (
                result.get("categories") or result.get("spus")
            ):
                payloads.append(result)
    finally:
        await page.close()
    return payloads


# ── Menu create (catalog sync writer) ────────────────────────────────────────
# The Keeta item create/update endpoint. `saveSpu` both creates (no id) and updates
# (the Edit form uses the same verb). VERIFIED live 2026-09-01 end-to-end through the
# wired `create_keeta_spu` + `delete_keeta_spu` on shop 1644170195/DSO: create `code 0`,
# item found in the menu read, `deleteSpu code 0`, re-read gone — no orphan.
# NOTE: saveSpu/deleteSpu/listConfig run via `page.evaluate` (MAIN world), not
# `evaluate_in_page` — they need the page's own restaurant context; the isolated call
# answers 107000106 "Restaurant ID required". Two non-obvious fields are REQUIRED (each surfaced as the next
# validation error while walking the chain):
#   • `categoryId` — the PLATFORM backend category (后台类目). Without it → 107000901
#     "backend category information is not filled". Value = the shop's `categoryId`
#     from `common/r/listConfig` (`_keeta_backend_category_id` reads it per shop).
#   • `sourceLanguageType` ("en") + the translate-type fields. Without them → 107000632
#     "The original language type is empty".
# (The nine name-guesses spuCategoryId/backendCategoryId/secondCategoryId/… all failed;
# the real field is plain `categoryId` with the listConfig value — found by walking the
# validation chain, not the read shape.) A sync-created item is `status=0` (off-shelf)
# so it is never live before review. Only reached behind CATALOG_SYNC_ENABLED.
KEETA_SPU_SAVE_ENDPOINT = "/api/sailorProduct/spu/w/saveSpu"


def build_keeta_spu_payload(
    shop_id: Any,
    *,
    name: str,
    category_id: Any,
    backend_category_id: Any,
    price: Any,
    currency: str = "AED",
    name_ar: str | None = None,
    active: bool = False,
) -> dict:
    """The saveSpu body for a NEW item — the exact shape a live create-then-delete
    accepted (code 0) on 2026-09-01. Pure + unit-tested.

    Two fields beyond the obvious ones are REQUIRED or saveSpu rejects:
      • ``categoryId`` — the platform **backend category** (后台类目). Without it →
        `107000901 "backend category information is not filled"`. This is
        ``backend_category_id`` (the shop's `categoryId` from `common/r/listConfig`;
        `create_keeta_spu` fetches it when not supplied).
      • ``sourceLanguageType`` (+ the translate-type fields) — without it →
        `107000632 "The original language type is empty"`.
    ``category_id`` is the shop menu section (`shopCategoryIdList`). `active=False`
    writes it off-shelf (status 0) so a sync never makes an item live unreviewed;
    `availableTime` is the form default (always available, Mon–Sun 00:00-23:59)."""
    return {
        "shopId": int(shop_id),
        "name": name,
        "nameI18n": {"en": name, "ar": name_ar or name},
        "status": 1 if active else 0,
        "categoryId": int(backend_category_id),
        "shopCategoryIdList": [int(category_id)],
        "type": 1,
        "sourceLanguageType": "en",
        "targetLanguageType": None,
        "nameTranslateType": 0,
        "descriptionTranslateType": 0,
        "descSourceLanguageType": "en",
        "skuList": [
            {
                "spec": "",
                "price": str(price),
                "currency": currency,
                "status": 1,
                "sequence": 1,
                "sourceLanguageType": "en",
                "specTranslateType": 0,
            }
        ],
        "availableTime": {"code": 1, "values": ["00:00-23:59"] * 7},
    }


# The in-page signed create — the page's own `fetch` (mtgsig), same headers as the
# menu read. Kept identical so the signed session is recognised.
_SAVE_SPU_JS = """
async ({ endpoint, shopId, payload }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": String(shopId),
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const response = await fetch(endpoint, {
    method: "POST", credentials: "include", headers, body: JSON.stringify(payload)
  });
  const text = await response.text();
  try { return JSON.parse(text); } catch (e) { return { status: response.status, text }; }
}
"""


#: The item-editor config — its `data.categoryId` is the shop's platform backend
#: category (后台类目) that saveSpu requires. Suffix appended at call time.
KEETA_LISTCONFIG_PATH = "/api/sailorProduct/common/r/listConfig"

#: The order-LIST route. Priming on it sets the "restaurant" context that `listConfig`
#: and `saveSpu` need — the order-HISTORY route does NOT (listConfig then 107000106
#: "Restaurant ID required"). Verified live: create works from this route, not history.
KEETA_ORDER_LIST_ROUTE = (
    "https://merchant.mykeeta.com/order-manager/m/web/mach/"
    "b_pc_order_list?containerType=orderManager"
)

# A generic signed in-page POST (mtgsig), for the config read the create needs.
_POST_JSON_JS = """
async ({ endpoint, shopId, body }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": String(shopId),
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const r = await fetch(endpoint, { method: "POST", credentials: "include", headers,
    body: JSON.stringify(body || {}) });
  const t = await r.text();
  try { return JSON.parse(t); } catch (e) { return { status: r.status, text: t }; }
}
"""


async def _keeta_backend_category_id(page: Any, shop_id: Any) -> int | None:
    """The shop's platform backend category id (`listConfig.data.categoryId`) — the
    `categoryId` saveSpu requires. Read in-page (signed)."""
    # page.evaluate (main world) — listConfig needs the page's own restaurant context;
    # via evaluate_in_page's isolated call it returns 107000106 "Restaurant ID required".
    cfg = await page.evaluate(
        _POST_JSON_JS,
        {
            "endpoint": KEETA_LISTCONFIG_PATH + KEETA_QUERY_SUFFIX,
            "shopId": int(shop_id),
            "body": {"shopId": int(shop_id)},
        },
    )
    data = cfg.get("data") if isinstance(cfg, dict) else None
    cat = (data or {}).get("categoryId") if isinstance(data, dict) else None
    return int(cat) if cat is not None else None


async def create_keeta_spu(
    context: Any,
    *,
    shop_id: Any,
    name: str,
    category_id: Any,
    price: Any,
    currency: str = "AED",
    active: bool = False,
    backend_category_id: Any | None = None,
) -> dict:
    """Create one Keeta menu item in-page (mtgsig-signed saveSpu). Returns the raw
    response (`{code, message, data}`; code 0 = success). Off-shelf by default.

    Payload confirmed live 2026-09-01 by a controlled create-then-delete (code 0,
    then deleted, no orphan). The **backend category** (`categoryId`) is required —
    if not supplied it is read from `listConfig`. Only invoked behind
    CATALOG_SYNC_ENABLED. Primes on the order-LIST route — it sets both LOGIN_ACCOUNTID
    and the "restaurant" context that listConfig + saveSpu require (the product route
    alone leaves LOGIN_ACCOUNTID unset; the order-HISTORY route leaves listConfig with
    "Restaurant ID required"). All verified live."""
    page = await context.new_page()
    try:
        await page.goto(
            KEETA_ORDER_LIST_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        account_id = ""
        for _ in range(12):  # poll up to ~24s for the SPA to prime the session
            await page.wait_for_timeout(2_000)
            await _reseed_keeta_storage(page)
            account_id = await evaluate_in_page(page, _LOGIN_ACCOUNTID_JS)
            if account_id:
                break
        if not account_id:
            from .browser import NeedsHumanLogin

            raise NeedsHumanLogin("keeta create: in-page session signed out")
        if backend_category_id is None:
            # Best-effort auto-resolve. listConfig needs the page's "restaurant"
            # context, which the hydrated session does not always carry headlessly
            # (it then answers 107000106 "Restaurant ID required"). A couple of
            # product-page reloads usually settle it; if not, the caller must pass it.
            for _ in range(3):
                backend_category_id = await _keeta_backend_category_id(page, shop_id)
                if backend_category_id is not None:
                    break
                await page.goto(
                    KEETA_MENU_ROUTE, wait_until="domcontentloaded", timeout=60_000
                )
                await page.wait_for_timeout(4_000)
            if backend_category_id is None:
                raise ValueError(
                    "keeta create: could not resolve the backend categoryId from "
                    "listConfig — pass backend_category_id explicitly (the shop's "
                    "`listConfig.data.categoryId`; 6669 for MM's Keeta shops)."
                )
        payload = build_keeta_spu_payload(
            shop_id,
            name=name,
            category_id=category_id,
            backend_category_id=backend_category_id,
            price=price,
            currency=currency,
            active=active,
        )
        # page.evaluate (main world) — saveSpu needs the page's restaurant context.
        return await page.evaluate(
            _SAVE_SPU_JS,
            {
                "endpoint": KEETA_SPU_SAVE_ENDPOINT,
                "shopId": int(shop_id),
                "payload": payload,
            },
        )
    finally:
        await page.close()


#: Item delete — verified live 2026-09-01: `deleteSpu` returns a *validation* error
#: ("shopId not exist!") for a bad id, while `delSpu`/`removeSpu`/`offShelf` return
#: "no matched api config found". So this is the real remove verb; it de-lists the
#: item entirely (used to reverse the controlled create-then-delete).
KEETA_SPU_DELETE_ENDPOINT = "/api/sailorProduct/spu/w/deleteSpu"

_DELETE_SPU_JS = """
async ({ endpoint, shopId, spuId }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": String(shopId),
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const response = await fetch(endpoint, {
    method: "POST", credentials: "include", headers,
    body: JSON.stringify({ shopId: Number(shopId), spuId: Number(spuId) })
  });
  const text = await response.text();
  try { return JSON.parse(text); } catch (e) { return { status: response.status, text }; }
}
"""


async def delete_keeta_spu(context: Any, *, shop_id: Any, spu_id: Any) -> dict:
    """Delete one Keeta menu item in-page (mtgsig-signed `deleteSpu`). Returns the
    raw response (`code` 0 = success). This reverses the controlled create-then-delete
    that confirms the create payload, so a verification run never leaves an orphan."""
    page = await context.new_page()
    try:
        # Prime on the order-LIST route (sets LOGIN_ACCOUNTID + restaurant context),
        # the same as create — the product route alone leaves LOGIN_ACCOUNTID unset.
        await page.goto(
            KEETA_ORDER_LIST_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        account_id = ""
        for _ in range(12):  # poll up to ~24s for the SPA to prime the session
            await page.wait_for_timeout(2_000)
            await _reseed_keeta_storage(page)
            account_id = await evaluate_in_page(page, _LOGIN_ACCOUNTID_JS)
            if account_id:
                break
        if not account_id:
            from .browser import NeedsHumanLogin

            raise NeedsHumanLogin("keeta delete: in-page session signed out")
        # page.evaluate (main world) — deleteSpu needs the page's restaurant context.
        return await page.evaluate(
            _DELETE_SPU_JS,
            {
                "endpoint": KEETA_SPU_DELETE_ENDPOINT,
                "shopId": int(shop_id),
                "spuId": int(spu_id),
            },
        )
    finally:
        await page.close()


# ── Business hours (catalog-sync hours read) ─────────────────────────────────
# Verified live 2026-09-01: the merchant portal exposes shop hours through the SCM
# summary endpoint the order page calls — `POST shop/base/summary/list {shopIdList}`
# returns, per shop, `businessStatus` (1=open) and `todayBusinessHours`
# [{startTime,endTime}] in SECONDS-from-midnight (28800=08:00, 84600=23:30). This is
# TODAY's window only — the portal does not expose a full weekly schedule on this
# account, so a Keeta hours read is "open/closed + today's window", not a 7-day
# schedule. The signed fetch must run from a primed page (LOGIN_ACCOUNTID is set by
# the ORDER route, not the product route — see fetch_keeta_menu).
# Suffix appended at call time — KEETA_QUERY_SUFFIX is defined lower (finance block).
KEETA_SHOP_SUMMARY_PATH = "/api/scm/gw/shop/base/summary/list"
KEETA_ORDER_ROUTE_HOURS = KEETA_ORDER_HISTORY_ROUTE  # primes LOGIN_ACCOUNTID

_SHOP_SUMMARY_JS = """
async ({ endpoint, shopIdList }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const r = await fetch(endpoint, { method: "POST", credentials: "include", headers,
    body: JSON.stringify({ shopIdList }) });
  const t = await r.text();
  try { return JSON.parse(t); } catch (e) { return { status: r.status, text: t }; }
}
"""


async def fetch_keeta_today_hours(context: Any, shop_ids: list[str]) -> list[dict]:
    """Per-shop `{shopId, businessStatus, todayBusinessHours}` read in-page (signed).

    `todayBusinessHours` is `[{startTime,endTime}]` in seconds-from-midnight (today
    only — see the endpoint note above). Returns the raw `shopList` for the API's
    `parse_keeta_today_hours`. Navigates the ORDER route first: it primes
    LOGIN_ACCOUNTID, which the product route alone does not (verified live)."""
    page = await context.new_page()
    try:
        await page.goto(
            KEETA_ORDER_ROUTE_HOURS, wait_until="domcontentloaded", timeout=60_000
        )
        account_id = ""
        for _ in range(12):  # poll up to ~24s for the SPA to prime the session
            await page.wait_for_timeout(2_000)
            await _reseed_keeta_storage(page)
            account_id = await evaluate_in_page(page, _LOGIN_ACCOUNTID_JS)
            if account_id:
                break
        if not account_id:
            logger.error("keeta hours: in-page session signed out (no LOGIN_ACCOUNTID)")
            return []
        result = await evaluate_in_page(
            page,
            _SHOP_SUMMARY_JS,
            {
                "endpoint": KEETA_SHOP_SUMMARY_PATH + KEETA_QUERY_SUFFIX,
                "shopIdList": [str(s) for s in shop_ids],
            },
        )
        data = result.get("data") if isinstance(result, dict) else None
        shops = (data or {}).get("shopList") if isinstance(data, dict) else None
        return shops if isinstance(shops, list) else []
    finally:
        await page.close()


# ── Business hours write (catalog-sync hours write) ──────────────────────────
# Live capture 2026-09-04 from merchant.mykeeta.com/m/web/shop#/settings (logged-in
# Chrome + shop SPA `825.cf93c.js` / `shop-normal-time-editor.3697b.js`):
#   GET  /api/scm/business-hour/effective-data/get?shopId=… →
#        {mon:[{startTime,endTime,option:1}], … sun:[…]}  (seconds-from-midnight)
#   POST /api/scm/business-hour/update
#        {shopId, businessHourOfTheWeek: <that weekly map>}
# Closed day is `[{startTime:0,endTime:0,option:1}]`, not an empty list. A 23:59
# end is stored as 86400. Holiday/special dates are a different verb
# (`/api/scm/special-business-hour/update`) — not this job.
KEETA_HOURS_SETTINGS_ROUTE = "https://merchant.mykeeta.com/m/web/shop#/settings"
#: Captured save verb (shop-normal-time-editor `zr({businessHourOfTheWeek}, shopId)`).
#: Query suffix is appended at call time (`KEETA_QUERY_SUFFIX`).
KEETA_HOURS_SAVE_PATH = "/api/scm/business-hour/update"
#: Matching weekly read the save body is built from.
KEETA_HOURS_EFFECTIVE_GET_PATH = "/api/scm/business-hour/effective-data/get"
#: Weekday keys on `businessHourOfTheWeek` — Sunday first, matching MM 0=Sunday.
_KEETA_DAY_KEYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def keeta_today_day_key(now: datetime | None = None) -> str:
    """Today's `businessHourOfTheWeek` key on the shop clock (Dubai)."""
    local = now or datetime.now(_BUSINESS_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_BUSINESS_TZ)
    else:
        local = local.astimezone(_BUSINESS_TZ)
    # datetime.weekday: Mon=0 … Sun=6 → our Sunday-first tuple.
    return _KEETA_DAY_KEYS[(local.weekday() + 1) % 7]


def _keeta_hour_slot(
    start_time: int | None, end_time: int | None, *, closed: bool, option: int = 1
) -> dict[str, int]:
    if closed or start_time is None or end_time is None:
        return {"startTime": 0, "endTime": 0, "option": option}
    end = int(end_time)
    if end == 86340:
        end = 86400
    return {"startTime": int(start_time), "endTime": end, "option": option}


def build_keeta_hours_write_body(
    shop_id: Any,
    *,
    closed: bool,
    start_time: int | None = None,
    end_time: int | None = None,
    weekly: dict[str, Any] | None = None,
    mm_weekly: dict[str, Any] | None = None,
    day_key: str | None = None,
) -> dict:
    """POST body for `/api/scm/business-hour/update`.

    Captured shape: `{shopId, businessHourOfTheWeek:{mon:[{startTime,endTime,
    option}],…}}`.

    Two modes:
      * **Full week from MM** (`mm_weekly` given): mirror MM's whole schedule —
        all seven days come from `mm_weekly` (keys `sun`..`sat`, already
        `[{startTime,endTime,option}]` slots, a closed day `[{0,0,1}]`). This is
        the source-of-truth sync. The portal `weekly` read-back only fills a day
        MM omitted, so a shop is never blanked.
      * **Today overlay** (no `mm_weekly`, the legacy path): overlay `day_key`
        (default today DXB) onto the portal `weekly` so the other six days are
        not wiped; if `weekly` is missing, fill all seven with the same slot.
    """
    sid = str(shop_id)
    week: dict[str, Any] = {}
    if isinstance(weekly, dict):
        for key in _KEETA_DAY_KEYS:
            existing = weekly.get(key)
            if isinstance(existing, list) and existing:
                week[key] = existing
    if isinstance(mm_weekly, dict):
        for key in _KEETA_DAY_KEYS:
            slots = mm_weekly.get(key)
            if isinstance(slots, list) and slots:
                week[key] = slots
        # A day neither MM nor the portal gave becomes closed rather than absent.
        closed_slot = _keeta_hour_slot(None, None, closed=True)
        for key in _KEETA_DAY_KEYS:
            week.setdefault(key, [closed_slot])
        return {"shopId": sid, "businessHourOfTheWeek": week}
    slot = _keeta_hour_slot(start_time, end_time, closed=closed)
    today = day_key or keeta_today_day_key()
    week[today] = [slot]
    for key in _KEETA_DAY_KEYS:
        week.setdefault(key, [slot])
    return {"shopId": sid, "businessHourOfTheWeek": week}


def looks_like_keeta_hours_save(url: str, method: str = "POST") -> bool:
    """True for a POST/PUT that looks like a shop-hours / status save, not the list read."""
    if str(method).upper() not in ("POST", "PUT"):
        return False
    low = url.lower()
    if (
        "summary/list" in low
        or "effective-data/get" in low
        or "offline-data/get" in low
    ):
        return False
    if "business-hour/update" in low:
        return True
    has_write = any(mark in low for mark in ("save", "update", "edit", "/w/"))
    has_hours = any(
        mark in low
        for mark in ("hour", "business", "opentime", "closetime", "shop/base")
    )
    return has_write and has_hours


def looks_like_keeta_shop_write(url: str, method: str = "POST") -> bool:
    """Any shop-gateway POST/PUT except the summary/list read.

    The probe records these so a click-Save is visible even when the path does
    not yet match `looks_like_keeta_hours_save`. A hours body is never POSTed
    to one of these unless it also looks like a hours save (or
    `KEETA_HOURS_SAVE_PATH` is set).
    """
    if str(method).upper() not in ("POST", "PUT"):
        return False
    low = url.lower()
    if "summary/list" in low:
        return False
    return "/api/scm/gw/" in low


_HOURS_WRITE_JS = """
async ({ endpoint, shopId, payload }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": String(shopId),
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const response = await fetch(endpoint, {
    method: "POST", credentials: "include", headers, body: JSON.stringify(payload)
  });
  const text = await response.text();
  try { return JSON.parse(text); } catch (e) { return { status: response.status, text }; }
}
"""

_HOURS_READ_JS = """
async ({ endpoint, shopId }) => {
  const headers = {
    "accept": "application/json, text/plain, */*",
    "accountid": sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
    "shopid": String(shopId),
    "cityid": sessionStorage.getItem("cityId") || "",
    "region": sessionStorage.getItem("region") || "AE",
    "opcenterselectedregion": sessionStorage.getItem("region") || "AE"
  };
  const response = await fetch(endpoint, {
    method: "GET", credentials: "include", headers
  });
  const text = await response.text();
  try { return JSON.parse(text); } catch (e) { return { status: response.status, text }; }
}
"""


def _hours_endpoint(path: str, shop_id: str | None = None) -> str:
    """Relative merchant path + the captured yoda/csec query suffix."""
    url = path + KEETA_QUERY_SUFFIX
    if shop_id:
        url += f"&shopId={shop_id}"
    return url


async def _post_keeta_hours(
    page: Any, *, shop_id: str, payload: dict[str, Any], save_path: str
) -> dict[str, Any]:
    raw = await page.evaluate(
        _HOURS_WRITE_JS,
        {
            "endpoint": save_path,
            "shopId": str(shop_id),
            "payload": payload,
        },
    )
    return raw if isinstance(raw, dict) else {"raw": raw}


async def write_keeta_today_hours(
    context: Any,
    *,
    windows: list[dict[str, Any]] | None = None,
    wait_seconds: int = 8,
    persist: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Prime the persistent session, then POST `/api/scm/business-hour/update`.

    Returns `{saved, probed, captured_xhrs, all_shop_posts, save_path, results,
    todo?}`; each `results` item is `{shopId, raw|planned|skipped}`.
    `windows` items are `{shop_id|shopId, weekly}` (a full MM week — the
    source-of-truth mirror), or the legacy `{shop_id, closed, start_time,
    end_time}` today-overlay.

    `persist=False` (the CLI probe) never POSTs — it only listens for a save
    XHR. The daemon passes `persist=True`: GET each shop's weekly map, build the
    body (full week from `weekly` when given, else today's overlay), and POST
    `{shopId, businessHourOfTheWeek}`. `dry_run=True` builds and records the body
    but POSTs nothing (the VM enumeration pass). An empty GET is not POSTed back
    (that would wipe the week).
    """
    from .browser import NeedsHumanLogin

    page = await context.new_page()
    captured: list[str] = []
    seen_posts: list[str] = []
    save_path = KEETA_HOURS_SAVE_PATH.strip()
    listen_ms = 0 if persist else max(0, int(wait_seconds) * 1000)

    def _on_request(request: Any) -> None:
        url = str(getattr(request, "url", "") or "")
        method = str(getattr(request, "method", "GET") or "GET")
        path = url.split("?", 1)[0]
        if looks_like_keeta_shop_write(url, method) and path not in seen_posts:
            seen_posts.append(path)
        if looks_like_keeta_hours_save(url, method) and path not in captured:
            captured.append(path)

    try:
        await page.goto(
            KEETA_ORDER_ROUTE_HOURS, wait_until="domcontentloaded", timeout=60_000
        )
        account_id = ""
        for _ in range(12):
            await page.wait_for_timeout(2_000)
            await _reseed_keeta_storage(page)
            account_id = str(
                await evaluate_in_page(page, _LOGIN_ACCOUNTID_JS) or ""
            ).strip()
            if account_id:
                break
        if not account_id:
            raise NeedsHumanLogin(
                "keeta hours write: LOGIN_ACCOUNTID is empty — persistent "
                "profile is signed out. Run: aggregator-bootstrap login --channel keeta"
            )

        page.on("request", _on_request)
        try:
            await page.goto(
                KEETA_HOURS_SETTINGS_ROUTE,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            if listen_ms:
                await page.wait_for_timeout(listen_ms)
        finally:
            try:
                page.remove_listener("request", _on_request)
            except Exception:  # noqa: BLE001 — fakes / already-removed
                pass

        if not save_path and captured:
            save_path = captured[0]
            logger.info("keeta hours: captured save XHR %s", save_path)
        elif seen_posts:
            logger.info(
                "keeta hours: shop POSTs seen (none hours-save shaped): %s",
                seen_posts,
            )

        if save_path and "yodaReady=" not in save_path:
            save_path = save_path + KEETA_QUERY_SUFFIX

        saved = 0
        results: list[dict[str, Any]] = []
        by_shop: dict[str, dict[str, Any]] = {}
        for window in windows or []:
            shop_id = window.get("shop_id") or window.get("shopId")
            if shop_id:
                by_shop[str(shop_id)] = window

        shop_ids = list(by_shop)
        if persist and not shop_ids:
            shop_ids = await _read_shop_ids(page)

        should_write = persist or bool(by_shop)
        if save_path and should_write:
            for shop_id in shop_ids:
                weekly: dict[str, Any] | None = None
                get_url = _hours_endpoint(KEETA_HOURS_EFFECTIVE_GET_PATH, shop_id)
                got = await page.evaluate(
                    _HOURS_READ_JS, {"endpoint": get_url, "shopId": str(shop_id)}
                )
                data = got.get("data") if isinstance(got, dict) else None
                if not isinstance(data, dict) and isinstance(got, dict):
                    data = got
                if isinstance(data, dict) and isinstance(
                    data.get("businessHourOfTheWeek"), dict
                ):
                    data = data["businessHourOfTheWeek"]
                if isinstance(data, dict) and any(
                    isinstance(data.get(k), list) for k in _KEETA_DAY_KEYS
                ):
                    weekly = data
                window = by_shop.get(shop_id)
                mm_weekly = window.get("weekly") if isinstance(window, dict) else None
                if mm_weekly:
                    # Full-week mirror of MM's source of truth (all seven days).
                    payload = build_keeta_hours_write_body(
                        shop_id, closed=False, weekly=weekly, mm_weekly=mm_weekly
                    )
                elif window is None:
                    if not weekly:
                        logger.warning(
                            "keeta hours: shop %s GET returned no weekly map; skip",
                            shop_id,
                        )
                        results.append(
                            {"shopId": str(shop_id), "raw": got, "skipped": True}
                        )
                        continue
                    payload = {
                        "shopId": str(shop_id),
                        "businessHourOfTheWeek": weekly,
                    }
                else:
                    payload = build_keeta_hours_write_body(
                        shop_id,
                        closed=bool(window.get("closed")),
                        start_time=window.get("start_time", window.get("startTime")),
                        end_time=window.get("end_time", window.get("endTime")),
                        weekly=weekly,
                    )
                if dry_run:
                    # Enumerate only: record the body we would POST, touch nothing.
                    logger.info("keeta hours dry-run shop %s", shop_id)
                    results.append(
                        {"shopId": str(shop_id), "planned": payload, "dry_run": True}
                    )
                    continue
                # page.evaluate (main world) — same as saveSpu; the isolated
                # world 107000106s without the restaurant context.
                raw = await _post_keeta_hours(
                    page, shop_id=str(shop_id), payload=payload, save_path=save_path
                )
                results.append({"shopId": str(shop_id), "raw": raw})
                code = raw.get("code") if isinstance(raw, dict) else None
                if code == 0:
                    saved += 1
                else:
                    logger.warning(
                        "keeta hours write shop %s did not return code 0: %s",
                        shop_id,
                        raw,
                    )

        out: dict[str, Any] = {
            "saved": saved,
            "probed": True,
            "captured_xhrs": captured,
            "all_shop_posts": seen_posts,
            "save_path": save_path or None,
            "results": results,
        }
        if not save_path:
            out["todo"] = (
                "keeta hours save XHR not yet captured; sat on "
                f"{KEETA_HOURS_SETTINGS_ROUTE} for {wait_seconds}s and recorded "
                f"{len(captured)} hours-save POSTs / {len(seen_posts)} shop "
                "POSTs. On the VM (one Chrome — stop the daemon if it holds "
                "keeta.chrome): docker compose -f docker-compose.prod.yml run "
                "--rm aggregator-worker probe-keeta-hours-save --wait-seconds 90"
                " — attach CDP, click Save on Shop hours, paste the printed "
                "path into KEETA_HOURS_SAVE_PATH."
            )
            logger.warning("keeta hours: %s", out["todo"])
        return out
    finally:
        await page.close()


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
        await _reseed_keeta_storage(page)

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
