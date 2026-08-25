"""The Foodics write-back decisions, tested without a DB or a live Foodics.

This is the write-back that replaced GrubOps' `order-force-*` overrides: when MM
moves an aggregator order, we drive the Foodics order through the actions its
console exposes — dispatch (ready), close (delivered), decline (pending cancel)
and void (accepted cancel). What lives here is the decision logic: which Foodics
call each MM move makes, what it skips as already done, and how a stuck push is
retried.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.order import OrderStatusEnum
from app.services.foodics import foodics_orders_service as f
from app.services.providers.foodics_provider import (
    _ACCEPT_LANGUAGE,
    _CHROME_UA,
    DELIVERY_DELIVERED,
    DELIVERY_READY,
    FoodicsAuthError,
    FoodicsClient,
    FoodicsConfig,
)


def _fake_db(scalar_one_or_none=None):
    class _Result:
        def scalar_one_or_none(self):
            return scalar_one_or_none

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    db.flush = AsyncMock()
    return db


def _map(**kw):
    kw.setdefault("foodics_order_id", "f1")
    kw.setdefault("last_pushed_status", None)
    kw.setdefault("last_push_error", None)
    return SimpleNamespace(**kw)


# ── mirror_status_out: which Foodics call each MM move makes ──────────────────


@pytest.mark.asyncio
async def test_packed_dispatches_when_not_already_dispatched():
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 1}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.PACKED, actor="pos"
        )
    fp.update_delivery_status.assert_awaited_once_with("f1", DELIVERY_READY)
    assert order_map.last_pushed_status == "packed"
    assert order_map.last_push_error is None


@pytest.mark.asyncio
async def test_packed_accepts_a_still_pending_order_before_dispatch():
    # The console's Dispatch button is hidden until Accept. A packed MM order
    # whose Foodics twin is still pending has to take both steps.
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 1, "delivery_status": None}),
        accept_order=AsyncMock(),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.PACKED, actor="pos"
        )
    fp.accept_order.assert_awaited_once_with("f1")
    fp.update_delivery_status.assert_awaited_once_with("f1", DELIVERY_READY)
    assert order_map.last_pushed_status == "packed"


@pytest.mark.asyncio
async def test_packed_is_idempotent_when_already_dispatched():
    # Already ready/assigned/en-route: no second dispatch, but still a success.
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 3}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.PACKED, actor="pos"
        )
    fp.update_delivery_status.assert_not_awaited()
    assert order_map.last_pushed_status == "packed"


@pytest.mark.asyncio
async def test_delivered_marks_delivered_then_closes():
    # The 5-minute auto-close: delivery_status = 5, then Close Order (status 4).
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 2}),
        update_delivery_status=AsyncMock(),
        close_order=AsyncMock(),
        decline_order=AsyncMock(),
        void_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.DELIVERED, actor="system"
        )
    fp.update_delivery_status.assert_awaited_once_with("f1", DELIVERY_DELIVERED)
    fp.close_order.assert_awaited_once_with("f1")
    assert order_map.last_pushed_status == "delivered"


@pytest.mark.asyncio
async def test_delivered_skips_close_when_foodics_is_already_done():
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 4, "delivery_status": 5}),
        update_delivery_status=AsyncMock(),
        close_order=AsyncMock(),
        decline_order=AsyncMock(),
        void_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.DELIVERED, actor="system"
        )
    fp.update_delivery_status.assert_not_awaited()
    fp.close_order.assert_not_awaited()
    assert order_map.last_pushed_status == "delivered"


@pytest.mark.asyncio
async def test_cancel_declines_a_still_pending_order():
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 1, "delivery_status": None}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="admin"
        )
    fp.decline_order.assert_awaited_once_with("f1")
    assert order_map.last_pushed_status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_voids_an_already_accepted_order():
    # Void Order on an Active Foodics order — status 7, not decline.
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 1}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
        void_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="pos"
        )
    fp.decline_order.assert_not_awaited()
    fp.void_order.assert_awaited_once_with("f1")
    assert order_map.last_pushed_status == "cancelled"
    assert order_map.last_push_error is None


@pytest.mark.asyncio
async def test_cancel_is_idempotent_when_foodics_already_voided():
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 7, "delivery_status": None}),
        decline_order=AsyncMock(),
        void_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="pos"
        )
    fp.void_order.assert_not_awaited()
    fp.decline_order.assert_not_awaited()
    assert order_map.last_pushed_status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_records_a_closed_order_it_cannot_void():
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 4, "delivery_status": 5}),
        decline_order=AsyncMock(),
        void_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="pos"
        )
    fp.void_order.assert_not_awaited()
    assert order_map.last_pushed_status is None
    assert "already closed" in order_map.last_push_error


@pytest.mark.asyncio
async def test_a_move_with_no_foodics_id_yet_is_recorded_not_pushed():
    order_map = _map(foodics_order_id=None)
    db = _fake_db(order_map)
    fp = SimpleNamespace(get_order=AsyncMock())
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.PACKED, actor="pos"
        )
    fp.get_order.assert_not_awaited()
    assert order_map.last_push_error == "no Foodics order id yet"


@pytest.mark.asyncio
async def test_an_unmirrored_status_does_nothing():
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(get_order=AsyncMock())
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db,
            mm_order_id="mm1",
            new_status=OrderStatusEnum.ARRIVED_AT_POS,
            actor="aggregator",
        )
    fp.get_order.assert_not_awaited()


# ── the retry sweep: re-fire a push the immediate mirror-out never landed ─────


def _sweep_db(rows, order_map=None):
    """One result serves both the candidate query (`.all()`) and the map lookup
    `mirror_status_out` makes next (`.scalar_one_or_none()`)."""

    class _Result:
        def all(self):
            return rows

        def scalar_one_or_none(self):
            return order_map

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_a_stuck_packed_push_is_retried_and_lands():
    order = SimpleNamespace(id="mm1", status=OrderStatusEnum.PACKED)
    order_map = _map(last_push_error="no Foodics order id yet")
    db = _sweep_db([(order, order_map)], order_map=order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 1}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with (
        patch.object(f, "provider", fp),
        patch.object(f, "is_enabled", return_value=True),
    ):
        landed = await f.sweep_pending_pushouts(db)
    fp.update_delivery_status.assert_awaited_once_with("f1", DELIVERY_READY)
    assert order_map.last_pushed_status == "packed"
    assert landed == 1


@pytest.mark.asyncio
async def test_an_order_already_pushed_for_its_state_is_skipped():
    order = SimpleNamespace(id="mm1", status=OrderStatusEnum.PACKED)
    order_map = _map(last_pushed_status="packed")
    db = _sweep_db([(order, order_map)], order_map=order_map)
    fp = SimpleNamespace(get_order=AsyncMock(), update_delivery_status=AsyncMock())
    with (
        patch.object(f, "provider", fp),
        patch.object(f, "is_enabled", return_value=True),
    ):
        landed = await f.sweep_pending_pushouts(db)
    fp.get_order.assert_not_awaited()
    assert landed == 0


@pytest.mark.asyncio
async def test_the_retry_sweep_is_a_no_op_when_disabled():
    db = _sweep_db([])
    with patch.object(f, "is_enabled", return_value=False):
        landed = await f.sweep_pending_pushouts(db)
    assert landed == 0
    db.execute.assert_not_awaited()


# ── the provider builds the console core-api requests ────────────────────────


def _cfg(**kw):
    base = dict(
        console_base="https://console.foodics.com",
        account_number="862261",
        email="owner@x.com",
        password="pw",
        timeout=1,
    )
    base.update(kw)
    return FoodicsConfig(**base)


@pytest.mark.asyncio
async def test_dispatch_updates_delivery_status_via_the_updating_verb():
    captured = {}
    client = FoodicsClient(_cfg())

    async def fake_call(method, path, *, json_body=None, params=None, **_):
        captured.update(method=method, path=path, body=json_body)
        return {"data": {}}

    client._call = fake_call
    await client.update_delivery_status("f1", DELIVERY_READY)
    assert captured["method"] == "PUT"
    assert captured["path"] == "/core-api/updating"
    assert captured["body"]["url"] == "/orders/f1"
    assert captured["body"]["payload"]["delivery_status"] == DELIVERY_READY
    assert "dispatched_at" in captured["body"]["payload"]
    assert "delivered_at" not in captured["body"]["payload"]


@pytest.mark.asyncio
async def test_decline_writes_the_declined_status_via_updating():
    captured = {}
    client = FoodicsClient(_cfg())

    async def fake_call(method, path, *, json_body=None, params=None, **_):
        captured.update(method=method, path=path, body=json_body)
        return {"data": {}}

    client._call = fake_call
    await client.decline_order("f1")
    assert captured["path"] == "/core-api/updating"
    assert captured["body"]["url"] == "/orders/f1"
    assert captured["body"]["payload"] == {"status": 3}


@pytest.mark.asyncio
async def test_close_writes_done_status_and_closed_at():
    captured = {}
    client = FoodicsClient(_cfg())

    async def fake_call(method, path, *, json_body=None, params=None, **_):
        captured.update(method=method, path=path, body=json_body, params=params)
        if path == "/core-api/getting":
            return {"data": {"id": "user-1"}}
        return {"data": {}}

    client._call = fake_call
    await client.close_order("f1")
    assert captured["path"] == "/core-api/updating"
    assert captured["body"]["url"] == "/orders/f1"
    assert captured["body"]["payload"]["status"] == 4
    assert "closed_at" in captured["body"]["payload"]
    assert captured["body"]["payload"]["closer_id"] == "user-1"


@pytest.mark.asyncio
async def test_void_writes_status_seven_with_reason_and_reversing_payment():
    captured = {}
    client = FoodicsClient(_cfg())
    client._whoami_id = AsyncMock(return_value="user-1")
    client._void_reason_id = AsyncMock(return_value="reason-cancelled")
    client._order_for_write = AsyncMock(
        return_value={
            "business_date": "2026-08-25",
            "meta": {},
            "payments": [
                {
                    "payment_method": {"id": "pm-keeta"},
                    "user": {"id": "user-1"},
                    "amount": 40,
                    "tendered": 40,
                    "business_date": "2026-08-25",
                    "added_at": "2026-08-25 09:47:00",
                }
            ],
            "products": [
                {
                    "id": "line-1",
                    "product": {"id": "prod-1"},
                    "quantity": 1,
                    "unit_price": 40,
                    "total_price": 40,
                }
            ],
            "combos": [],
        }
    )

    async def fake_call(method, path, *, json_body=None, params=None, **_):
        captured.update(method=method, path=path, body=json_body)
        return {"data": {}}

    client._call = fake_call
    await client.void_order("f1")
    payload = captured["body"]["payload"]
    assert captured["body"]["url"] == "/orders/f1"
    assert payload["status"] == 7
    assert payload["total_price"] == 0
    assert payload["closer_id"] == "user-1"
    assert payload["payments"][-1]["amount"] == -40
    assert payload["payments"][-1]["payment_method_id"] == "pm-keeta"
    assert payload["products"][0]["status"] == 5
    assert payload["products"][0]["void_reason_id"] == "reason-cancelled"


@pytest.mark.asyncio
async def test_get_order_reads_via_the_getting_verb_and_unwraps_data():
    captured = {}
    client = FoodicsClient(_cfg())

    async def fake_call(method, path, *, json_body=None, params=None, **_):
        captured.update(method=method, path=path, params=params)
        return {"data": {"id": "f1", "status": 2, "delivery_status": 1}}

    client._call = fake_call
    order = await client.get_order("f1")
    assert captured["method"] == "GET"
    assert captured["path"] == "/core-api/getting"
    assert captured["params"]["url"] == "/orders"
    assert captured["params"]["id"] == "f1"
    assert order == {"id": "f1", "status": 2, "delivery_status": 1}


def test_credentials_configure_the_client():
    assert FoodicsClient(_cfg()).is_configured is True


def test_an_empty_client_is_not_configured():
    client = FoodicsClient(_cfg(email="", password=""))
    assert client.is_configured is False


_LOGIN_HTML = """
<html><head>
<meta name="csrf-token" content="csrf-from-meta">
</head><body>
<form method="POST" action="/login">
<input type="hidden" name="_token" value="csrf-from-form">
<input type="hidden" name="token" id="recaptcha_token" />
<input name="business"><input name="email"><input name="password">
</form>
<script src="https://www.google.com/recaptcha/api.js?render=sitekey-abc"></script>
</body></html>
"""

_DASHBOARD_HTML = """
<html><head>
<meta name="csrf-token" content="csrf-after-login-01234567890123456789">
</head><body><div id="app">console</div></body></html>
"""


class _FakeFoodicsHttp:
    """Stands in for httpx.AsyncClient during login: one GET /login, one POST."""

    def __init__(
        self, *, post_url="https://console.foodics.com/home", post_html=_DASHBOARD_HTML
    ):
        self.post_url = post_url
        self.post_html = post_html
        self.gets: list[dict] = []
        self.posts: list[dict] = []
        self.cookies = httpx.Cookies()
        self.cookies.set("XSRF-TOKEN", "xsrf")
        self.cookies.set("__Secure-console_session", "sess")
        self._kwargs: dict = {}

    def __call__(self, **kwargs):
        self._kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, path, **kwargs):
        self.gets.append({"path": path, "headers": kwargs.get("headers")})
        return httpx.Response(
            200,
            text=_LOGIN_HTML,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://console.foodics.com/login"),
        )

    async def post(self, path, **kwargs):
        self.posts.append(
            {
                "path": path,
                "data": kwargs.get("data"),
                "headers": kwargs.get("headers") or {},
            }
        )
        return httpx.Response(
            200,
            text=self.post_html,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", self.post_url),
        )


@pytest.mark.asyncio
async def test_login_posts_the_live_form_as_uae_chrome(monkeypatch):
    # Captured 2026-08-25: account number is `business`, recaptcha is `token`,
    # and the page is a regular form POST — not XHR, not `business_reference`.
    fake = _FakeFoodicsHttp()
    monkeypatch.setattr(
        "app.services.providers.foodics_provider.httpx.AsyncClient", fake
    )

    async def token(_self, _html):
        return "recaptcha-v3-token"

    client = FoodicsClient(_cfg())
    monkeypatch.setattr(FoodicsClient, "_recaptcha_token", token)
    await client._login()

    assert fake._kwargs["headers"]["User-Agent"] == _CHROME_UA
    assert fake._kwargs["headers"]["Accept-Language"] == _ACCEPT_LANGUAGE
    assert fake.posts, "login must POST"
    posted = fake.posts[0]
    assert posted["data"]["business"] == "862261"
    assert posted["data"]["email"] == "owner@x.com"
    assert posted["data"]["password"] == "pw"
    assert posted["data"]["token"] == "recaptcha-v3-token"
    assert posted["data"]["_token"] == "csrf-from-form"
    assert "business_reference" not in posted["data"]
    assert "g-recaptcha-response" not in posted["data"]
    assert posted["headers"]["User-Agent"] == _CHROME_UA
    assert posted["headers"]["Accept-Language"] == _ACCEPT_LANGUAGE
    assert posted["headers"].get("X-Requested-With") is None
    assert not any(k.lower().startswith("sec-fetch") for k in posted["headers"])
    assert client._cookie
    assert client._csrf == "csrf-after-login-01234567890123456789"


@pytest.mark.asyncio
async def test_a_200_that_stays_on_login_is_a_rejection(monkeypatch):
    # Laravel re-renders the form on a bad password. Treating that 200 as
    # success cached a guest session on prod and every later call 419'd.
    fake = _FakeFoodicsHttp(
        post_url="https://console.foodics.com/login", post_html=_LOGIN_HTML
    )
    monkeypatch.setattr(
        "app.services.providers.foodics_provider.httpx.AsyncClient", fake
    )

    async def token(_self, _html):
        return "tok"

    client = FoodicsClient(_cfg())
    monkeypatch.setattr(FoodicsClient, "_recaptcha_token", token)
    with pytest.raises(FoodicsAuthError, match="login failed"):
        await client._login()
    assert client._cookie is None


@pytest.mark.asyncio
async def test_a_failed_login_backs_off_instead_of_hammering():
    # A flood of failed logins locks the account, so a failure is remembered and
    # re-raised without another network round-trip until the cooldown passes.
    client = FoodicsClient(_cfg())
    calls = 0

    async def failing_login():
        nonlocal calls
        calls += 1
        raise FoodicsAuthError("nope", status=419)

    client._login = failing_login
    for _ in range(3):
        with pytest.raises(FoodicsAuthError):
            await client._ensure_session()
    assert calls == 1  # only the first attempt hit the network; the rest backed off
