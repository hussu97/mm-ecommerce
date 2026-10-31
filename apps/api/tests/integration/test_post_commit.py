"""`post_commit`: external side effects run after the COMMIT, never inside it.

A payment webhook holds the order row and the branch's inventory advisory lock
until it commits, and it used to await two Resend emails and an APNs push in
that window — p50 ~4s that every till sale at the branch waited behind. The
emails and the push are now registered with `post_commit.defer` and run once
the session commits. What has to hold, and what only a real session can prove:

* nothing runs before the commit, and by the time it runs the commit is durable
  (visible from another connection);
* a rollback — of the transaction, or of a savepoint the work was registered
  inside — drops it; so does closing the session without committing;
* releasing a savepoint is not a commit;
* with no transaction to wait for, it runs inline, as before.

And the two real callers: `payment_service._handle_payment_succeeded` (the
confirmation emails) and `push_service.notify_order_placed` (the register push).

Needs a database: set TEST_DATABASE_URL (or DATABASE_URL).
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import post_commit

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def eng():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def table(eng):
    name = f"post_commit_test_{uuid.uuid4().hex[:8]}"
    async with eng.begin() as c:
        await c.execute(text(f"CREATE TABLE {name} (val text)"))
    yield name
    async with eng.begin() as c:
        await c.execute(text(f"DROP TABLE {name}"))


class Recorder:
    """Deferred work that records whether the write it follows was durable."""

    def __init__(self, eng, table):
        self.eng, self.table, self.calls = eng, table, []

    def work(self, label: str):
        async def _run():
            async with self.eng.connect() as c:
                seen = await c.scalar(
                    text(f"SELECT count(*) FROM {self.table} WHERE val = :v"),
                    {"v": label},
                )
            self.calls.append((label, seen))

        return _run


async def _write(db: AsyncSession, table: str, label: str) -> None:
    await db.execute(text(f"INSERT INTO {table} (val) VALUES (:v)"), {"v": label})


async def test_runs_only_after_the_commit_and_sees_it_durable(eng, table):
    rec = Recorder(eng, table)
    async with AsyncSession(eng) as db:
        await _write(db, table, "a")
        await post_commit.defer(db, rec.work("a"), name="a")

        await post_commit.drain()
        assert rec.calls == [], "ran before the transaction committed"
        assert post_commit.pending(db) == ["a"]

        await db.commit()
        await post_commit.drain()

    # It ran, once, and the row it follows was already committed when it did.
    assert rec.calls == [("a", 1)]


async def test_a_rollback_drops_it(eng, table):
    rec = Recorder(eng, table)
    async with AsyncSession(eng) as db:
        await _write(db, table, "b")
        await post_commit.defer(db, rec.work("b"), name="b")
        await db.rollback()
        assert post_commit.pending(db) == []
        # A later commit on the same session must not resurrect it.
        await _write(db, table, "c")
        await db.commit()
        await post_commit.drain()
    assert rec.calls == []


async def test_closing_without_committing_drops_it(eng, table):
    rec = Recorder(eng, table)
    db = AsyncSession(eng)
    await _write(db, table, "d")
    await post_commit.defer(db, rec.work("d"), name="d")
    await db.close()
    await post_commit.drain()
    assert rec.calls == []


async def test_a_savepoint_rollback_drops_only_what_it_held(eng, table):
    """`handle_gateway_return` applies an event inside a savepoint and swallows
    its failure; the emails that event registered must go with it, while work
    registered outside the savepoint survives."""
    rec = Recorder(eng, table)
    async with AsyncSession(eng) as db:
        await _write(db, table, "outer")
        await post_commit.defer(db, rec.work("outer"), name="outer")
        with pytest.raises(RuntimeError):
            async with db.begin_nested():
                await _write(db, table, "inner")
                await post_commit.defer(db, rec.work("inner"), name="inner")
                raise RuntimeError("the apply failed")
        assert post_commit.pending(db) == ["outer"]
        await db.commit()
        await post_commit.drain()
    assert rec.calls == [("outer", 1)]


async def test_a_released_savepoint_is_not_a_commit(eng, table):
    rec = Recorder(eng, table)
    async with AsyncSession(eng) as db:
        await _write(db, table, "e")
        async with db.begin_nested():
            await post_commit.defer(db, rec.work("e"), name="e")
        # The savepoint is released; the outer transaction is still open.
        await post_commit.drain()
        assert rec.calls == []
        assert post_commit.pending(db) == ["e"]
        await db.rollback()
        await post_commit.drain()
    assert rec.calls == []


async def test_with_no_transaction_it_runs_inline(eng, table):
    rec = Recorder(eng, table)
    async with AsyncSession(eng) as db:
        await post_commit.defer(db, rec.work("f"), name="f")
        assert rec.calls == [("f", 0)]
    # And for something that is not a session at all (a unit test's mock).
    work = AsyncMock()
    await post_commit.defer(SimpleNamespace(), work, name="mock")
    work.assert_awaited_once()


async def test_failing_work_is_contained(eng, table):
    async def boom():
        raise RuntimeError("Resend is down")

    async with AsyncSession(eng) as db:
        await _write(db, table, "g")
        await post_commit.defer(db, boom, name="boom")
        await db.commit()  # does not raise
        await post_commit.drain()  # nor does this


# ── the callers ───────────────────────────────────────────────────────────────


@pytest.fixture
def confirmed(monkeypatch):
    """`_handle_payment_succeeded` with the transition and rendering stubbed:
    what is under test is when the emails go, not what confirming does."""
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_service

    async def transition(db, order, status, **_):
        order.status = status
        return True

    monkeypatch.setattr(payment_service.order_lifecycle, "transition", transition)
    monkeypatch.setattr(
        payment_service.order_service,
        "to_response",
        AsyncMock(return_value=SimpleNamespace(order_number="MM-20261101-001")),
    )
    sent = SimpleNamespace(confirmation=AsyncMock(), owner=AsyncMock())
    monkeypatch.setattr(
        payment_service.email_service, "send_order_confirmation", sent.confirmation
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_owner_order_notification", sent.owner
    )
    order = SimpleNamespace(
        order_number="MM-20261101-001",
        status=OrderStatusEnum.CREATED,
        payment_id=None,
        payment_provider="stripe",
        payment_transactions=[],
        total=10,
    )
    return SimpleNamespace(service=payment_service, order=order, sent=sent)


def _succeeded():
    from app.services.providers.base import GatewayEvent, PaymentEventType

    return GatewayEvent(
        event_id="evt_pc_1",
        event_type=PaymentEventType.SUCCEEDED,
        raw_type="payment_intent.succeeded",
        payment_id="pi_pc_1",
    )


async def test_the_webhook_sends_the_confirmation_after_its_commit(
    eng, table, confirmed
):
    async with AsyncSession(eng) as db:
        await _write(db, table, "order-row")  # the transaction the webhook holds
        await confirmed.service._handle_payment_succeeded(
            db, "stripe", confirmed.order, _succeeded()
        )
        await post_commit.drain()
        # Confirmed, but nobody told yet: the locks are still held.
        confirmed.sent.confirmation.assert_not_awaited()
        confirmed.sent.owner.assert_not_awaited()
        assert post_commit.pending(db) == ["confirmation-emails:MM-20261101-001"]

        await db.commit()
        await post_commit.drain()

    confirmed.sent.confirmation.assert_awaited_once()
    confirmed.sent.owner.assert_awaited_once()


async def test_a_webhook_that_rolls_back_tells_nobody(eng, table, confirmed):
    """A 500 the gateway will retry: the retry sends them, this attempt must not."""
    async with AsyncSession(eng) as db:
        await _write(db, table, "order-row")
        await confirmed.service._handle_payment_succeeded(
            db, "stripe", confirmed.order, _succeeded()
        )
        await db.rollback()
        await post_commit.drain()

    confirmed.sent.confirmation.assert_not_awaited()
    confirmed.sent.owner.assert_not_awaited()


async def test_the_register_push_goes_after_the_commit(eng, table, monkeypatch):
    from app.services import push_service

    monkeypatch.setattr(push_service, "is_enabled", lambda: True)
    transport = AsyncMock(return_value=1)
    monkeypatch.setattr(push_service, "_send_to_branch", transport)

    order = SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20261101-002",
        branch_id=uuid.uuid4(),
        total=42,
        check_number="0007",
        source="online",
        aggregator_channel=None,
        delivery_method=None,
        scheduled_date=None,
        scheduled_time_slot=None,
        customer_name=None,
        customer_phone=None,
    )
    monkeypatch.setattr(push_service, "_channel_line", lambda o: "Website")
    monkeypatch.setattr(push_service, "_when_line", lambda o: None)
    monkeypatch.setattr(push_service, "_customer_line", lambda o: None)

    async with AsyncSession(eng) as db:
        await _write(db, table, "order-row")
        await push_service.notify_order_placed(db, order)
        await post_commit.drain()
        transport.assert_not_awaited()
        await db.commit()
        await post_commit.drain()

    transport.assert_awaited_once()
    _, kwargs = transport.await_args
    assert kwargs["collapse_id"] == "order-MM-20261101-002"
