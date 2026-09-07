"""
An unauthenticated noon Send push must not move an order it only *names*.

noon Send do not sign requests, so the shared `X-API-Key` is the whole boundary
(`test_noon_send_webhook_api.py` pins that the key is now enforced). This file
pins the defence-in-depth half behind it (F-COU-2): even when a push is being
acted on without a validated key — the historical `NOON_SEND_ENFORCE_WEBHOOK_KEY`
= false posture, or anything that ever lets an unvalidated push through — it may
only resolve the task by the id we dispatched under (`order_nr` ->
`courier_order_id`, which we never publish). The `order_reference` fallbacks
match on values that leave our system: the short courier reference and, worse,
the human `MM-YYYYMMDD-NNN` order number, both of which are guessable. Those are
withheld unless the key validated, so a caller who guesses an order number moves
nothing.

The lookup half needs a real Postgres to prove which key matched which row, so
those classes are DB-gated (`TEST_DATABASE_URL`); CI runs a real postgres:16 and
`alembic upgrade head` before pytest, so they run there. The rate-limit class
needs no database.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order, OrderDelivery
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.models.webhook_event import WebhookEvent
from app.services.couriers import noon_send_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)

#: The task id we dispatched under — 17 characters we never publish.
TASK_ID = "EHG84NNJMVG35BTDE"
#: The short courier reference: `courier_reference` is VARCHAR(7).
SHORT_REF = "7654321"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    """A noon Send delivery with a task id, a short reference and an order number.

    Created and cleaned up on its own session. Yields the three handles a push
    might name it by, so a test can prove which of them an untrusted push is
    allowed to resolve.
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)
    order_number = f"NSA-{uuid.uuid4().hex[:12]}"  # VARCHAR(30)
    async with Session() as db:
        branch = Branch(
            name="noon-send-auth branch",
            reference=f"nsa-{uuid.uuid4().hex[:12]}",
        )
        db.add(branch)
        await db.flush()
        # A non-deleted branch must own exactly one default stock container at
        # commit (deferred trigger from migration 186).
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        order = Order(
            order_number=order_number,
            email="pytest-noon-send-auth@example.com",
            source="online",
            branch_id=branch.id,
            status=OrderStatusEnum.CONFIRMED,
            delivery_method=DeliveryMethodEnum.DELIVERY,
            subtotal=Decimal("100.00"),
            total=Decimal("110.00"),
            delivery_fee=Decimal("10.00"),
            shipping_address_snapshot={
                "latitude": 25.20,
                "longitude": 55.27,
                "phone": "+971501234567",
                "city": "Dubai",
            },
        )
        db.add(order)
        await db.flush()

        db.add(
            OrderDelivery(
                order_id=order.id,
                provider=noon_send_service.PROVIDER,
                courier_order_id=TASK_ID,
                courier_reference=SHORT_REF,
                fee_charged=Decimal("10.00"),
            )
        )
        await db.commit()
        order_id, branch_id = order.id, branch.id

    yield order_id, order_number

    async with Session() as db:
        await db.execute(
            WebhookEvent.__table__.delete().where(
                WebhookEvent.order_number == order_number
            )
        )
        await db.execute(
            OrderDelivery.__table__.delete().where(OrderDelivery.order_id == order_id)
        )
        await db.execute(Order.__table__.delete().where(Order.id == order_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


@requires_db
class TestUnvalidatedLookupIsTaskIdOnly:
    """`_delivery_for` restricts an untrusted push to the task-id path.

    The pure lookup, with no apply behind it — exactly the surface F-COU-2
    narrows.
    """

    @pytest.fixture
    async def db(self, engine):
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            yield session

    async def test_untrusted_by_order_number_resolves_nothing(self, db, seeded):
        """A guessed `MM-…` order number is inert without a validated key."""
        _order_id, order_number = seeded
        found = await noon_send_service._delivery_for(
            db, {"order_reference": order_number}, trusted=False
        )
        assert found is None

    async def test_untrusted_by_short_reference_resolves_nothing(self, db, seeded):
        found = await noon_send_service._delivery_for(
            db, {"order_reference": SHORT_REF}, trusted=False
        )
        assert found is None

    async def test_untrusted_by_task_id_still_resolves(self, db, seeded):
        """The task id we never publish is the one key an untrusted push may use."""
        order_id, _order_number = seeded
        found = await noon_send_service._delivery_for(
            db, {"order_nr": TASK_ID}, trusted=False
        )
        assert found is not None
        assert found.order_id == order_id

    async def test_trusted_by_order_number_resolves(self, db, seeded):
        """A validated push keeps the reference fallback — an early push can
        arrive with only the order number to go on."""
        order_id, order_number = seeded
        found = await noon_send_service._delivery_for(
            db, {"order_reference": order_number}, trusted=True
        )
        assert found is not None
        assert found.order_id == order_id

    async def test_trusted_by_short_reference_resolves(self, db, seeded):
        order_id, _order_number = seeded
        found = await noon_send_service._delivery_for(
            db, {"order_reference": SHORT_REF}, trusted=True
        )
        assert found is not None
        assert found.order_id == order_id


@requires_db
class TestHandleWebhookHonoursTrust:
    """The same guard seen through `handle_webhook`'s `matched` flag.

    `apply_webhook` is stubbed: the point here is *whether* a delivery was
    resolved, not the rider-refresh/routing work that follows a match (which
    reaches the noon API).
    """

    @pytest.fixture
    async def db(self, engine):
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            yield session

    @pytest.fixture
    def apply_spy(self, monkeypatch):
        calls = []

        async def spy(db, payload, delivery):
            calls.append(delivery)
            return delivery

        monkeypatch.setattr(noon_send_service, "apply_webhook", spy)
        return calls

    async def test_untrusted_push_by_order_number_moves_nothing(
        self, db, seeded, apply_spy
    ):
        _order_id, order_number = seeded
        result = await noon_send_service.handle_webhook(
            db,
            {
                "order_reference": order_number,
                "status_code": "picked_up",
                "timestamp": "2026-09-07 08:55:14",
            },
            trusted=False,
        )
        assert result.get("matched") is not True
        assert apply_spy == []

    async def test_trusted_push_by_task_id_is_applied(self, db, seeded, apply_spy):
        order_id, _order_number = seeded
        result = await noon_send_service.handle_webhook(
            db,
            {
                "order_nr": TASK_ID,
                "status_code": "picked_up",
                "timestamp": "2026-09-07 09:10:00",
            },
            trusted=True,
        )
        assert result.get("matched") is True
        assert [d.order_id for d in apply_spy] == [order_id]


class TestTheRoutesAreRateLimited:
    """Both noon Send routes carry a per-minute cap (F-COU-2).

    An open, unmetered endpoint that resolves orders is a place to sit and guess
    task ids at line rate; a modest per-IP cap closes that. Asserted by
    introspecting the registered limits rather than firing a hundred requests at
    a shared limiter that the rest of the suite also uses.
    """

    def _limits_for(self, name: str):
        import app.api.v1.webhooks  # noqa: F401 — registers the decorators
        from app.core.limiter import limiter

        return limiter._route_limits.get(f"app.api.v1.webhooks.{name}", [])

    @pytest.mark.parametrize(
        "route",
        ["noon_send_webhook", "noon_send_tracking_webhook"],
    )
    def test_route_has_a_per_minute_limit(self, route):
        limits = self._limits_for(route)
        assert limits, f"{route} carries no rate limit"
        for limit in limits:
            # A per-minute window (60s expiry) with a positive, sane cap.
            assert limit.limit.get_expiry() == 60
            assert 0 < limit.limit.amount <= 600
