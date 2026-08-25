"""The Foodics write-back decisions, tested without a DB or a live Foodics.

This is the write-back that replaced GrubOps' `order-force-*` overrides: when MM
moves an aggregator order, we drive the Foodics order through the actions its
public API exposes — dispatch (ready), finalise (delivered), decline (pending
only). What lives here is the decision logic: which Foodics call each MM move
makes, what it skips as already done, and how a stuck push is retried.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.order import OrderStatusEnum
from app.services.foodics import foodics_orders_service as f
from app.services.providers.foodics_provider import (
    DELIVERY_DELIVERED,
    DELIVERY_READY,
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
async def test_delivered_finalises_on_the_delivery_axis():
    # The 5-minute auto-close: mark the Foodics order delivered (the API has no
    # public status=4 close).
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 2}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.DELIVERED, actor="system"
        )
    fp.update_delivery_status.assert_awaited_once_with("f1", DELIVERY_DELIVERED)
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
async def test_cancel_records_an_already_accepted_order_it_cannot_void():
    # Foodics has no public void once accepted — record it, do not claim success.
    order_map = _map()
    db = _fake_db(order_map)
    fp = SimpleNamespace(
        get_order=AsyncMock(return_value={"status": 2, "delivery_status": 1}),
        update_delivery_status=AsyncMock(),
        decline_order=AsyncMock(),
    )
    with patch.object(f, "provider", fp):
        await f.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="admin"
        )
    fp.decline_order.assert_not_awaited()
    assert order_map.last_pushed_status is None
    assert "cannot cancel" in order_map.last_push_error


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
    assert captured["body"]["url"] == "/orders"
    assert captured["body"]["id"] == "f1"
    assert captured["body"]["data"]["delivery_status"] == DELIVERY_READY
    assert "dispatched_at" in captured["body"]["data"]
    assert "delivered_at" not in captured["body"]["data"]


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
    assert captured["body"] == {"url": "/orders", "id": "f1", "data": {"status": 3}}


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


def test_the_recaptcha_token_is_an_empty_placeholder_until_built():
    # Not solved/forged/spoofed — an empty token, so the login still POSTs and we
    # can observe what the console does with a captcha-less request. Foodics is
    # expected to reject it until the real step is built.
    assert FoodicsClient(_cfg())._recaptcha_token() == ""


@pytest.mark.asyncio
async def test_a_failed_login_backs_off_instead_of_hammering():
    # A flood of failed logins locks the account, so a failure is remembered and
    # re-raised without another network round-trip until the cooldown passes.
    from app.services.providers.foodics_provider import FoodicsAuthError

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
