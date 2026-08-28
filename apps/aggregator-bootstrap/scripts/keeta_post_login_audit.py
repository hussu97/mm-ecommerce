"""After a headed Keeta login: dump shops, session keys, and finance network.

Run with the same STORAGE_STATE_DIR used for login, e.g.:

  STORAGE_STATE_DIR=.aggregator-sessions .venv/bin/python scripts/keeta_post_login_audit.py

Writes JSON under .aggregator-sessions/keeta-audit/ (gitignored via sessions dir).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from aggregator_bootstrap.engine import evaluate_in_page
from aggregator_bootstrap.keeta_pull import (
    _GET_ORDERS_JS,
    _SHOP_IDS_JS,
    KEETA_ORDER_HISTORY_ENDPOINT,
    KEETA_ORDER_HISTORY_ROUTE,
)

OUT = Path(os.environ.get("STORAGE_STATE_DIR", ".aggregator-sessions")) / "keeta-audit"
STATE = Path(os.environ.get("STORAGE_STATE_DIR", ".aggregator-sessions")) / "keeta.session.json"
EXTRA = Path(os.environ.get("STORAGE_STATE_DIR", ".aggregator-sessions")) / "keeta.extra.json"

SHOP_ENDPOINTS = (
    "/api/account/query/getShopListByAccountV2",
    "/api/account/query/getShopListByAccount",
    "/api/scm/shop/list",
)

FINANCE_ROUTES = (
    "https://merchant.mykeeta.com/web/app/finance",
    "https://merchant.mykeeta.com/web/settle",
    "https://merchant.mykeeta.com/web/app/finance#/main/download",
)

KNOWN_BRANCH_MAP = {
    "1644174206": "sharjah",
    "1644189187": "barsha_heights",
    "1644170195": "dso",
    "1644336388": "karama",
}

_FETCH_JS = """
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
  try { return { status: response.status, json: JSON.parse(text) }; }
  catch (e) { return { status: response.status, text: text.slice(0, 5000) }; }
}
"""

_SESSION_JS = """
() => {
  const keys = {};
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i);
    keys[k] = sessionStorage.getItem(k);
  }
  return {
    url: location.href,
    region: sessionStorage.getItem("region"),
    cityId: sessionStorage.getItem("cityId"),
    LOGIN_ACCOUNTID: sessionStorage.getItem("LOGIN_ACCOUNTID"),
    LOGIN_UID: sessionStorage.getItem("LOGIN_UID"),
    SHOP_IDS: sessionStorage.getItem("SHOP_IDS"),
    keys,
  };
}
"""


def _dump(name: str, payload: object) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {path}")
    return path


def _parse_shop_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    text = str(raw).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
    except json.JSONDecodeError:
        pass
    return [p.strip() for p in text.split(",") if p.strip()]


def _session_storage_init_script(by_origin: dict) -> str:
    payload = json.dumps(by_origin)
    return f"""
() => {{
  const byOrigin = {payload};
  const items = byOrigin[location.origin];
  if (!items) return;
  for (const [key, value] of Object.entries(items)) {{
    try {{ sessionStorage.setItem(key, value); }} catch (e) {{}}
  }}
}}
"""


async def main() -> None:
    if not STATE.is_file():
        raise SystemExit(f"missing session state: {STATE}")

    extra: dict = {}
    if EXTRA.is_file():
        loaded = json.loads(EXTRA.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            extra = loaded

    captured: list[dict] = []

    def on_response(response) -> None:
        url = response.url.lower()
        if not any(
            frag in url
            for frag in (
                "finance",
                "settle",
                "bill",
                "statement",
                "payment",
                "payout",
                "download",
                "invoice",
                "commission",
                "shop",
                "account",
            )
        ):
            return

        async def _read() -> None:
            try:
                ctype = response.headers.get("content-type", "")
                if "json" in ctype:
                    payload = await response.json()
                else:
                    text = await response.text()
                    payload = {"text": text[:20_000]}
                captured.append(
                    {"url": response.url, "status": response.status, "payload": payload}
                )
            except Exception as exc:  # noqa: BLE001
                captured.append(
                    {"url": response.url, "status": response.status, "error": str(exc)}
                )

        asyncio.create_task(_read())

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=str(STATE))
        if extra:
            await context.add_init_script(_session_storage_init_script(extra))
        page = await context.new_page()
        page.on("response", on_response)

        await page.goto(
            "https://merchant.mykeeta.com/?region=AE",
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        await page.wait_for_timeout(5_000)
        session = await page.evaluate(_SESSION_JS)
        _dump(
            "session_storage.json",
            {
                "url": session.get("url"),
                "region": session.get("region"),
                "has_account": bool(session.get("LOGIN_ACCOUNTID")),
                "has_shops": bool(session.get("SHOP_IDS")),
                "shop_ids": _parse_shop_ids(session.get("SHOP_IDS")),
                "key_count": len(session.get("keys") or {}),
            },
        )

        # Shop discovery via known account APIs (mtgsig-signed in page).
        shops: list[dict] = []
        shop_raw: dict[str, object] = {}
        for endpoint in SHOP_ENDPOINTS:
            result = await evaluate_in_page(
                page, _FETCH_JS, {"endpoint": endpoint, "payload": {}}
            )
            shop_raw[endpoint] = result
            payload = (result or {}).get("json") if isinstance(result, dict) else None
            if isinstance(payload, dict):
                data = payload.get("data")
                candidates = (
                    data
                    if isinstance(data, list)
                    else (
                        data.get("list") or data.get("shopList") or []
                        if isinstance(data, dict)
                        else []
                    )
                )
                if isinstance(candidates, list):
                    for row in candidates:
                        if isinstance(row, dict) and (
                            row.get("shopId")
                            or row.get("storeId")
                            or row.get("merchantId")
                        ):
                            shops.append(row)
        _dump("shop_api_raw.json", shop_raw)
        _dump("shops.json", shops)

        # Compare to seeded branch map.
        found_ids = sorted(
            {
                str(row.get("shopId") or row.get("storeId") or row.get("merchantId"))
                for row in shops
                if row.get("shopId") or row.get("storeId") or row.get("merchantId")
            }
        )
        session_shop_ids = _parse_shop_ids(session.get("SHOP_IDS"))
        if not found_ids and session_shop_ids:
            found_ids = list(session_shop_ids)
        mapping = {
            "session_SHOP_IDS": session_shop_ids,
            "api_shop_ids": found_ids,
            "seeded_branch_map": KNOWN_BRANCH_MAP,
            "matched": {
                sid: KNOWN_BRANCH_MAP[sid]
                for sid in found_ids
                if sid in KNOWN_BRANCH_MAP
            },
            "api_only": [sid for sid in found_ids if sid not in KNOWN_BRANCH_MAP],
            "seed_only": [sid for sid in KNOWN_BRANCH_MAP if sid not in found_ids],
            "shop_names": [
                {
                    "shopId": str(
                        r.get("shopId") or r.get("storeId") or r.get("merchantId")
                    ),
                    "shopName": r.get("shopName")
                    or r.get("storeName")
                    or r.get("name"),
                    "shopCode": r.get("shopCode"),
                    "cityName": r.get("cityName") or r.get("city"),
                    "address": r.get("address") or r.get("shopAddress"),
                }
                for r in shops
            ],
        }
        _dump("branch_map_diff.json", mapping)
        print(json.dumps(mapping, indent=2, default=str))

        # Orders smoke: one page for first shop if any.
        shop_ids = await evaluate_in_page(page, _SHOP_IDS_JS) or []
        if not shop_ids and found_ids:
            shop_ids = found_ids[:1]
        await page.goto(
            KEETA_ORDER_HISTORY_ROUTE, wait_until="domcontentloaded", timeout=90_000
        )
        await page.wait_for_timeout(6_000)
        if shop_ids:
            now = datetime.now(timezone.utc)
            start = int(
                datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1000
            )
            end = int(now.timestamp() * 1000)
            orders = await evaluate_in_page(
                page,
                _GET_ORDERS_JS,
                {
                    "endpoint": KEETA_ORDER_HISTORY_ENDPOINT,
                    "payload": {
                        "startTime": start,
                        "endTime": end,
                        "orderType": 0,
                        "pageNum": 1,
                        "pageSize": 5,
                        "seqNoStr": "",
                        "shopIds": [
                            int(shop_ids[0])
                            if str(shop_ids[0]).isdigit()
                            else shop_ids[0]
                        ],
                        "merchantOpType": 29,
                    },
                },
            )
            _dump("orders_sample.json", orders)

        # Finance surfaces — navigate and let the response listener collect APIs.
        for route in FINANCE_ROUTES:
            print(f"goto {route}")
            await page.goto(route, wait_until="domcontentloaded", timeout=90_000)
            await page.wait_for_timeout(10_000)
            for label in ("Download", "Commission", "Invoice", "Settlement", "Bill"):
                loc = page.get_by_text(label, exact=False)
                try:
                    if await loc.count():
                        await loc.first.click(timeout=2_000)
                        await page.wait_for_timeout(3_000)
                except Exception:  # noqa: BLE001
                    pass

        await page.wait_for_timeout(5_000)
        # Summarize network without dumping full payloads to stdout.
        summary = [
            {
                "url": row.get("url"),
                "status": row.get("status"),
                "keys": list((row.get("payload") or {}).keys())[:20]
                if isinstance(row.get("payload"), dict)
                else None,
                "error": row.get("error"),
            }
            for row in captured
        ]
        _dump("finance_network.json", captured)
        _dump("finance_network_summary.json", summary)
        print(f"captured {len(captured)} finance/shop network responses")
        for row in summary[:30]:
            print(row.get("status"), (row.get("url") or "")[:120])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
