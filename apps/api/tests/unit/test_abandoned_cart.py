"""Abandoned-cart recovery: provider resume links and the sweep's selection.

The provider tests pin the one rule that keeps a dead link out of a customer's
inbox — a resume URL is offered only while the gateway session can still take a
payment. The sweep test pins which orders are picked (online, still `created`, in
the age window, with an email) and that each is mailed once.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.providers.stripe_provider import provider as stripe_provider
from app.services.providers.ziina_provider import provider as ziina_provider


def _order(**over):
    base = dict(
        payment_id="cs_test_123", order_number="MM-1", payment_provider="stripe"
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── Stripe resume_url ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stripe_resume_url_open_unpaid_returns_url(monkeypatch):
    import app.services.providers.stripe_provider as sp

    monkeypatch.setattr(sp.StripeProvider, "_configure", staticmethod(lambda: None))
    monkeypatch.setattr(
        sp.stripe.checkout.Session,
        "retrieve",
        lambda sid: SimpleNamespace(
            status="open", payment_status="unpaid", url="https://pay/x"
        ),
    )
    assert await stripe_provider.resume_url(_order()) == "https://pay/x"


@pytest.mark.asyncio
async def test_stripe_resume_url_skips_complete_or_paid(monkeypatch):
    import app.services.providers.stripe_provider as sp

    monkeypatch.setattr(sp.StripeProvider, "_configure", staticmethod(lambda: None))
    monkeypatch.setattr(
        sp.stripe.checkout.Session,
        "retrieve",
        lambda sid: SimpleNamespace(
            status="complete", payment_status="paid", url="https://pay/x"
        ),
    )
    assert await stripe_provider.resume_url(_order()) is None


@pytest.mark.asyncio
async def test_stripe_resume_url_ignores_non_session_id():
    # A non-`cs_` id never hits the network and never resumes.
    assert await stripe_provider.resume_url(_order(payment_id="pi_123")) is None


# ── Ziina resume_url ───────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp


@pytest.mark.asyncio
async def test_ziina_resume_url_pending_returns_redirect(monkeypatch):
    import app.services.providers.ziina_provider as zp

    monkeypatch.setattr(zp.settings, "ZIINA_API_KEY", "k", raising=False)
    resp = _FakeResp(
        200,
        {"status": "requires_payment_instrument", "redirect_url": "https://ziina/x"},
    )
    monkeypatch.setattr(zp.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))
    assert (
        await ziina_provider.resume_url(_order(payment_provider="ziina"))
        == "https://ziina/x"
    )


@pytest.mark.asyncio
async def test_ziina_resume_url_skips_completed(monkeypatch):
    import app.services.providers.ziina_provider as zp

    monkeypatch.setattr(zp.settings, "ZIINA_API_KEY", "k", raising=False)
    resp = _FakeResp(200, {"status": "completed", "redirect_url": "https://ziina/x"})
    monkeypatch.setattr(zp.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))
    assert await ziina_provider.resume_url(_order(payment_provider="ziina")) is None


# ── Sweep selection (DB-gated) ─────────────────────────────────────────────────
DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark_db = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@pytest.mark.asyncio
@pytestmark_db
async def test_sweep_mails_only_orders_in_the_window(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.base import utcnow
    from app.models.branch import Branch
    from app.models.inventory import Warehouse
    from app.models.order import Order, OrderStatusEnum
    from app.models.pos_order import OrderSourceEnum
    from app.services import email_service
    from app.services.orders import abandoned_checkout_service as acs
    from app.services.orders import order_service

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    marker = f"CART-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    # A committed branch needs its one default warehouse (deferred trigger, mig 186).
    branch = Branch(name=marker, reference=f"{marker[:40]}")
    async with Session() as s:
        s.add(branch)
        await s.flush()
        s.add(Warehouse(branch_id=branch.id, name=marker, is_default=True))
        await s.commit()
    branch_id = branch.id

    def mk(minutes_old, **over):
        base = dict(
            order_number=f"{marker}-{uuid.uuid4().hex[:6]}",
            email="c@example.com",
            locale="en",
            delivery_method="delivery",
            order_type="delivery",
            status=OrderStatusEnum.CREATED,
            source=OrderSourceEnum.ONLINE.value,
            branch_id=branch_id,
            payment_provider="stripe",
            payment_id="cs_live_x",
            created_at=now - timedelta(minutes=minutes_old),
            subtotal=Decimal("50"),
            total=Decimal("50"),
            vat_amount=Decimal("0"),
            total_excl_vat=Decimal("50"),
            vat_rate=Decimal("0"),
            discount_amount=Decimal("0"),
        )
        base.update(over)
        return Order(**base)

    async with Session() as s:
        fresh = mk(30)  # too new (< 60m)
        due = mk(120)  # in window
        old = mk(60 * 40)  # too old (> 23h)
        no_email = mk(120, email="")  # no address
        s.add_all([fresh, due, old, no_email])
        await s.commit()

    # Isolate the sweep: real DB read (this test's own engine), but mock the
    # network edges and the advisory lock — the real lock rides the global
    # scheduler engine, whose event loop is closed by the time the full suite
    # reaches here (it passes in isolation). The lock itself is covered elsewhere.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _always_mine(*a, **k):
        yield True

    monkeypatch.setattr(acs.advisory_lock, "held", _always_mine)
    monkeypatch.setattr(acs, "AsyncSessionFactory", Session)
    monkeypatch.setattr(
        order_service, "to_response", AsyncMock(side_effect=lambda db, o: o)
    )
    monkeypatch.setattr(email_service, "already_sent", AsyncMock(return_value=False))
    sent = AsyncMock()
    monkeypatch.setattr(email_service, "send_abandoned_cart", sent)
    monkeypatch.setattr(
        acs,
        "PROVIDERS",
        {"stripe": SimpleNamespace(resume_url=AsyncMock(return_value="https://pay/x"))},
    )

    try:
        moved = await acs.sweep_once(now=now)
        mailed = {call.args[0].order_number for call in sent.await_args_list}
        assert due.order_number in mailed
        assert fresh.order_number not in mailed
        assert old.order_number not in mailed
        assert no_email.order_number not in mailed
        assert moved == 1
    finally:
        from sqlalchemy import delete as sql_delete

        async with Session() as s:
            for o in (fresh, due, old, no_email):
                obj = await s.get(Order, o.id)
                if obj:
                    await s.delete(obj)
            await s.execute(
                sql_delete(Warehouse).where(Warehouse.branch_id == branch_id)
            )
            b = await s.get(Branch, branch_id)
            if b:
                await s.delete(b)
            await s.commit()
        await engine.dispose()
