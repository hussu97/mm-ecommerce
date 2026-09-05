"""The Keeta in-page pull, with `page.evaluate` mocked (no browser, no network).

Two things are proven here: (1) `fetch_keeta_orders` drives the in-page fetch and
returns the raw `getOrders` payloads unchanged, and (2) those payloads satisfy the
parse contract — the mm-ecommerce `keeta_provider.parse_orders` turns the sample
into orders with line items, so the shape the worker pushes is the shape the API
expects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The mm-ecommerce API package lives two levels up (apps/api); it holds the
# provider whose parse contract we assert against. Added to the path so `app`
# imports when the tests run under the api venv.
_API_ROOT = Path(__file__).resolve().parents[2] / "api"

from aggregator_bootstrap.keeta_pull import fetch_keeta_orders  # noqa: E402

# A realistic getOrders response: `data.list[]` of the Keeta envelope
# (baseOrder / merchantOrder / products / feeDtl.merchantFee) that parse_orders
# flattens. totalCount == 1 so pagination stops after the first page.
SAMPLE_GET_ORDERS = {
    "code": 0,
    "data": {
        "totalCount": 1,
        "list": [
            {
                "baseOrder": {
                    "orderViewId": "70012345",
                    "status": "completed",
                    "orderCreateTime": 1_723_363_200_000,
                },
                "merchantOrder": {"shopId": "123", "orderAmount": 4500},
                "products": [
                    {
                        "name": "Chocolate Cake",
                        "count": 1,
                        "price": 4500,
                        "skuId": "sku-1",
                    }
                ],
                "feeDtl": {"merchantFee": {"commission": 675, "total": 3825}},
            }
        ],
    },
}


class _FakePage:
    """Stands in for a Playwright page: evaluate returns SHOP_IDS then payloads."""

    def __init__(self, payload: dict, shop_ids: list[str]) -> None:
        self._payload = payload
        self._shop_ids = shop_ids
        self.evaluate_calls: list[tuple[str, object]] = []
        self.closed = False

    async def goto(self, *args, **kwargs) -> None:
        return None

    async def wait_for_timeout(self, *args, **kwargs) -> None:
        return None

    async def evaluate(self, script: str, arg: object = None):
        self.evaluate_calls.append((script, arg))
        if "LOGIN_ACCOUNTID" in script and "fetch" not in script:
            return "acct-1"
        if "SHOP_IDS" in script:
            return list(self._shop_ids)
        return self._payload

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    """Stands in for a Playwright browser context."""

    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page


async def test_fetch_keeta_orders_returns_raw_payloads_via_page_evaluate():
    page = _FakePage(SAMPLE_GET_ORDERS, ["123"])
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)

    # One shop, one (current-month) window, one page -> exactly one raw payload,
    # handed back untouched.
    assert payloads == [SAMPLE_GET_ORDERS]
    assert payloads[0] is SAMPLE_GET_ORDERS
    # The order fetch went through the in-page `fetch` (page.evaluate with an
    # endpoint/payload arg), not any out-of-page HTTP client.
    assert any(
        arg and isinstance(arg, dict) and arg.get("endpoint", "").endswith("getOrders")
        for _, arg in page.evaluate_calls
    )
    assert page.closed


async def test_no_shop_ids_still_makes_one_combined_call():
    page = _FakePage(SAMPLE_GET_ORDERS, [])
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)
    # With SHOP_IDS empty we still attempt one call (shopIds: []).
    assert payloads == [SAMPLE_GET_ORDERS]


def _load_keeta_provider():
    if str(_API_ROOT) not in sys.path:
        sys.path.insert(0, str(_API_ROOT))
    # The provider (and the models it imports) needs the mm-ecommerce API deps
    # (sqlalchemy, the app package). Those are absent from the worker's own venv,
    # so this test SKIPS cleanly there and only runs in the CI job that installs
    # apps/api alongside the worker (see .github/workflows/pr-check.yml).
    return pytest.importorskip(
        "app.services.providers.keeta_provider",
        reason="mm-ecommerce API deps (sqlalchemy + app package) not installed",
    )


async def test_fetched_payload_matches_keeta_provider_parse_contract():
    page = _FakePage(SAMPLE_GET_ORDERS, ["123"])
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)

    keeta_provider = _load_keeta_provider()
    orders = keeta_provider.provider.parse_orders(payloads[0])

    assert orders, "sample getOrders payload should parse into at least one order"
    order = orders[0]
    assert order.external_order_id == "70012345"
    assert order.external_outlet_id == "123"
    assert order.items and order.items[0].item_name == "Chocolate Cake"


# ── shop-id resolution: account extras preferred, hard-coded set the last resort


class _FakeResolvePage:
    """A page for `_resolve_shop_ids`: the V2 POST and the SHOP_IDS reader only."""

    def __init__(self, *, v2_response: object, session_shop_ids: list[str]) -> None:
        self._v2_response = v2_response
        self._session_shop_ids = session_shop_ids
        self.post_args: list[object] = []

    async def evaluate(self, script: str, arg: object = None):
        if "SHOP_IDS" in script:
            return list(self._session_shop_ids)
        self.post_args.append(arg)
        return self._v2_response


async def test_resolve_shop_ids_prefers_account_extras_over_the_constant():
    from aggregator_bootstrap import keeta_pull as kp

    page = _FakeResolvePage(v2_response={"data": []}, session_shop_ids=[])
    ids = await kp._resolve_shop_ids(page, fallback_shop_ids=["999", "888"])
    # The account's extras win; the hard-coded _KNOWN_SHOP_IDS is not used.
    assert ids == ["999", "888"]
    assert ids != list(kp._KNOWN_SHOP_IDS)


async def test_resolve_shop_ids_falls_back_to_known_constant_without_extras():
    from aggregator_bootstrap import keeta_pull as kp

    page = _FakeResolvePage(v2_response={"data": []}, session_shop_ids=[])
    ids = await kp._resolve_shop_ids(page)
    # No live ids, no sessionStorage, no extras → the historical constant stands.
    assert ids == list(kp._KNOWN_SHOP_IDS)


async def test_resolve_shop_ids_live_v2_wins_over_extras_and_constant():
    from aggregator_bootstrap import keeta_pull as kp

    page = _FakeResolvePage(
        v2_response={"data": [{"shopId": "111"}]}, session_shop_ids=[]
    )
    ids = await kp._resolve_shop_ids(page, fallback_shop_ids=["999"])
    assert ids == ["111"]


async def test_resolve_shop_ids_sends_customer_id_in_the_v2_body():
    from aggregator_bootstrap import keeta_pull as kp

    page = _FakeResolvePage(v2_response={"data": []}, session_shop_ids=[])
    await kp._resolve_shop_ids(page, customer_id="330066")
    assert page.post_args, "expected a V2 shop-list POST"
    assert page.post_args[0]["payload"] == {"customerId": "330066"}


# ── page-loop guard: a runaway totalCount is stopped at the hard cap ──────────


class _CapPage:
    """A page whose getOrders always returns a full page and an unreachable total."""

    def __init__(self) -> None:
        self.get_orders_calls = 0
        self.closed = False

    async def goto(self, *args, **kwargs) -> None:
        return None

    async def wait_for_timeout(self, *args, **kwargs) -> None:
        return None

    async def evaluate(self, script: str, arg: object = None):
        if "LOGIN_ACCOUNTID" in script and "fetch" not in script:
            return "acct-1"
        if "SHOP_IDS" in script:
            return ["123"]
        self.get_orders_calls += 1
        # totalCount never satisfied → only the cap can stop the loop.
        return {"data": {"totalCount": 10**9, "list": [{"x": 1}]}}

    async def close(self) -> None:
        self.closed = True


async def test_order_pagination_stops_at_the_page_cap():
    from aggregator_bootstrap import keeta_pull as kp

    page = _CapPage()
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)
    # One shop, one (current-month) window: exactly _MAX_ORDER_PAGES fetches.
    assert page.get_orders_calls == kp._MAX_ORDER_PAGES
    assert len(payloads) == kp._MAX_ORDER_PAGES
    assert page.closed


class _SignedOutPage:
    """A page whose LOGIN_ACCOUNTID is empty — the session is signed out."""

    def __init__(self) -> None:
        self.get_orders_calls = 0

    async def goto(self, *a, **k) -> None:
        return None

    async def wait_for_timeout(self, *a, **k) -> None:
        return None

    async def evaluate(self, script: str, arg: object = None):
        if "LOGIN_ACCOUNTID" in script and "fetch" not in script:
            return ""  # signed out
        self.get_orders_calls += 1
        return {"data": {"list": [{"x": 1}]}}

    async def close(self) -> None:
        return None


async def test_empty_login_accountid_raises_needs_login_not_a_silent_pull():
    from aggregator_bootstrap.browser import NeedsHumanLogin

    page = _SignedOutPage()
    with pytest.raises(NeedsHumanLogin):
        await fetch_keeta_orders(_FakeContext(page), months_back=0)
    # It must fail BEFORE firing a single risk-controlled getOrders fetch.
    assert page.get_orders_calls == 0


def test_build_keeta_spu_payload_off_shelf_and_priced():
    from decimal import Decimal

    from aggregator_bootstrap.keeta_pull import build_keeta_spu_payload

    p = build_keeta_spu_payload(
        123,
        name="ZZ Test",
        category_id=456,
        backend_category_id=6669,
        price=Decimal("35"),
        currency="AED",
    )
    assert p["shopId"] == 123
    assert p["name"] == "ZZ Test"
    assert p["status"] == 0  # off-shelf by default — never live before review
    assert p["shopCategoryIdList"] == [456]
    # The two fields the live create-then-delete proved are required:
    assert p["categoryId"] == 6669  # platform backend category (后台类目)
    assert p["sourceLanguageType"] == "en"  # else 107000632 "original language empty"
    assert p["skuList"][0]["price"] == "35"
    assert p["skuList"][0]["currency"] == "AED"
    assert p["skuList"][0]["sourceLanguageType"] == "en"
    # available all week (the Edit form's default), 7 day-slots.
    assert len(p["availableTime"]["values"]) == 7
    # active=True flips the shelf status.
    assert (
        build_keeta_spu_payload(
            1, name="x", category_id=2, backend_category_id=6669, price=1, active=True
        )["status"]
        == 1
    )


def test_build_keeta_hours_write_body_matches_captured_weekly_shape():
    """Captured 2026-09-04 from shop-normal-time-editor: `{shopId,
    businessHourOfTheWeek:{mon:[{startTime,endTime,option:1}],…}}`."""
    from aggregator_bootstrap.keeta_pull import (
        _KEETA_DAY_KEYS,
        build_keeta_hours_write_body,
    )

    weekly = {
        "mon": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "tue": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "wed": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "thu": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "fri": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "sat": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "sun": [{"startTime": 28800, "endTime": 84600, "option": 1}],
    }
    open_body = build_keeta_hours_write_body(
        "1644170195",
        closed=False,
        start_time=28800,
        end_time=84600,
        weekly=weekly,
        day_key="sun",
    )
    assert open_body["shopId"] == "1644170195"
    assert "businessStatus" not in open_body
    assert "todayBusinessHours" not in open_body
    week = open_body["businessHourOfTheWeek"]
    assert set(week) == set(_KEETA_DAY_KEYS)
    assert week["sun"] == [{"startTime": 28800, "endTime": 84600, "option": 1}]
    assert week["mon"] == weekly["mon"]

    closed_body = build_keeta_hours_write_body(
        "1644170195", closed=True, weekly=weekly, day_key="sun"
    )
    assert closed_body["businessHourOfTheWeek"]["sun"] == [
        {"startTime": 0, "endTime": 0, "option": 1}
    ]
    assert closed_body["businessHourOfTheWeek"]["mon"] == weekly["mon"]

    # UI 23:59 (86340) is stored as 86400; missing weekly fills all seven days.
    filled = build_keeta_hours_write_body(
        "1", closed=False, start_time=28800, end_time=86340, day_key="fri"
    )
    assert filled["businessHourOfTheWeek"]["fri"] == [
        {"startTime": 28800, "endTime": 86400, "option": 1}
    ]
    assert all(filled["businessHourOfTheWeek"][k] for k in _KEETA_DAY_KEYS)


def test_looks_like_keeta_hours_save_excludes_the_list_read():
    from aggregator_bootstrap.keeta_pull import (
        looks_like_keeta_hours_save,
        looks_like_keeta_shop_write,
    )

    assert looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/scm/business-hour/update?yodaReady=h5",
        "POST",
    )
    assert looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/scm/gw/shop/base/updateBusinessHours",
        "POST",
    )
    assert not looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/scm/gw/shop/base/summary/list", "POST"
    )
    assert not looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/scm/business-hour/effective-data/get",
        "POST",
    )
    assert not looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/scm/business-hour/offline-data/get",
        "POST",
    )
    assert not looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/order/history/getOrders", "POST"
    )
    assert looks_like_keeta_shop_write(
        "https://merchant.mykeeta.com/api/scm/gw/shop/base/somethingElse", "POST"
    )
    assert not looks_like_keeta_shop_write(
        "https://merchant.mykeeta.com/api/scm/gw/shop/base/summary/list", "POST"
    )
    assert not looks_like_keeta_hours_save(
        "https://merchant.mykeeta.com/api/scm/gw/shop/base/somethingElse", "POST"
    )


class _HoursPage:
    """A page for `write_keeta_today_hours`: primes LOGIN_ACCOUNTID, records gotos."""

    def __init__(
        self,
        *,
        account_id: str = "acct-1",
        emit_urls: list[tuple[str, str]] | None = None,
        weekly: dict | None = None,
        shop_ids: list[str] | None = None,
    ) -> None:
        self.account_id = account_id
        self.gotos: list[str] = []
        self.evaluate_calls: list[tuple[str, object]] = []
        self.closed = False
        self._request_handler = None
        self._emit_urls = emit_urls or []
        self.wait_ms: list[int] = []
        self.weekly = weekly
        self.shop_ids = shop_ids or []

    async def goto(self, url, *args, **kwargs) -> None:
        self.gotos.append(url)
        if self._request_handler and "m/web/shop" in url:
            for emit_url, method in self._emit_urls:
                self._request_handler(
                    type("Req", (), {"url": emit_url, "method": method})()
                )

    async def wait_for_timeout(self, ms, *args, **kwargs) -> None:
        self.wait_ms.append(int(ms))

    def on(self, event, handler) -> None:
        self._request_handler = handler

    def remove_listener(self, event, handler) -> None:
        self._request_handler = None

    async def evaluate(self, script: str, arg: object = None, **kwargs):
        self.evaluate_calls.append((script, arg))
        if "LOGIN_ACCOUNTID" in script and "fetch" not in script:
            return self.account_id
        if "SHOP_IDS" in script:
            return self.shop_ids
        if isinstance(arg, dict) and "payload" not in arg:
            return {"code": 0, "data": self.weekly}
        return {"code": 0}

    async def close(self) -> None:
        self.closed = True


class _HoursContext:
    def __init__(self, page: _HoursPage) -> None:
        self._page = page

    async def new_page(self) -> _HoursPage:
        return self._page


async def test_write_keeta_today_hours_probe_does_not_post():
    from aggregator_bootstrap.keeta_pull import (
        KEETA_HOURS_SAVE_PATH,
        KEETA_HOURS_SETTINGS_ROUTE,
        write_keeta_today_hours,
    )

    page = _HoursPage()
    result = await write_keeta_today_hours(
        _HoursContext(page), wait_seconds=8, persist=False
    )
    assert result["saved"] == 0
    assert result["probed"] is True
    assert KEETA_HOURS_SAVE_PATH.endswith("/api/scm/business-hour/update")
    assert result["save_path"] and "business-hour/update" in result["save_path"]
    assert "todo" not in result
    assert KEETA_HOURS_SETTINGS_ROUTE in page.gotos
    assert 8000 in page.wait_ms
    assert not any(
        isinstance(arg, dict) and "payload" in arg for _, arg in page.evaluate_calls
    )
    assert page.closed


async def test_write_keeta_today_hours_posts_when_save_path_and_windows():
    from aggregator_bootstrap import keeta_pull as kp

    page = _HoursPage(
        weekly={
            "mon": [{"startTime": 28800, "endTime": 84600, "option": 1}],
            "tue": [{"startTime": 28800, "endTime": 84600, "option": 1}],
            "wed": [{"startTime": 28800, "endTime": 84600, "option": 1}],
            "thu": [{"startTime": 28800, "endTime": 84600, "option": 1}],
            "fri": [{"startTime": 28800, "endTime": 84600, "option": 1}],
            "sat": [{"startTime": 28800, "endTime": 84600, "option": 1}],
            "sun": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        }
    )
    result = await kp.write_keeta_today_hours(
        _HoursContext(page),
        windows=[
            {
                "shop_id": "1644170195",
                "closed": False,
                "start_time": 28800,
                "end_time": 84600,
            }
        ],
        persist=False,
    )
    assert result["saved"] == 1
    assert "business-hour/update" in (result["save_path"] or "")
    payloads = [
        arg["payload"]
        for _, arg in page.evaluate_calls
        if isinstance(arg, dict) and isinstance(arg.get("payload"), dict)
    ]
    assert payloads
    assert payloads[0]["shopId"] == "1644170195"
    assert "businessHourOfTheWeek" in payloads[0]
    assert page.closed


async def test_write_keeta_today_hours_records_captured_save_xhr():
    from aggregator_bootstrap.keeta_pull import write_keeta_today_hours

    page = _HoursPage(
        emit_urls=[
            (
                "https://merchant.mykeeta.com/api/scm/business-hour/update?yodaReady=h5",
                "POST",
            )
        ]
    )
    result = await write_keeta_today_hours(
        _HoursContext(page), wait_seconds=1, persist=False
    )
    assert "business-hour/update" in (result["save_path"] or "")
    assert result["captured_xhrs"]
    assert "todo" not in result
    assert page.closed


async def test_write_keeta_today_hours_does_not_treat_unrelated_shop_post_as_save():
    from aggregator_bootstrap.keeta_pull import write_keeta_today_hours

    page = _HoursPage(
        emit_urls=[
            (
                "https://merchant.mykeeta.com/api/scm/gw/shop/base/somethingElse",
                "POST",
            )
        ]
    )
    result = await write_keeta_today_hours(
        _HoursContext(page), wait_seconds=1, persist=False
    )
    assert "business-hour/update" in (result["save_path"] or "")
    assert result["captured_xhrs"] == []
    assert any(path.endswith("somethingElse") for path in result["all_shop_posts"])
    assert "todo" not in result
    assert page.closed


async def test_write_keeta_today_hours_persist_identity_saves_weekly():
    from aggregator_bootstrap.keeta_pull import write_keeta_today_hours

    weekly = {
        "mon": [{"startTime": 28800, "endTime": 84600, "option": 1}],
        "sun": [{"startTime": 0, "endTime": 0, "option": 1}],
    }
    page = _HoursPage(weekly=weekly, shop_ids=["1644170195"])
    result = await write_keeta_today_hours(_HoursContext(page), persist=True)
    assert result["saved"] == 1
    payloads = [
        arg["payload"]
        for _, arg in page.evaluate_calls
        if isinstance(arg, dict) and isinstance(arg.get("payload"), dict)
    ]
    assert payloads == [{"shopId": "1644170195", "businessHourOfTheWeek": weekly}]
    assert 8000 not in page.wait_ms
    assert page.closed
