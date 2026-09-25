"""
Paymob, end to end, against a real database and a fake Paymob.

Everything here runs the real routes, the real `payment_service`, the real dedup
insert and the real commit; only Paymob itself (an in-memory fake speaking its
documented wire format) and the customer emails are stood in for. It walks one
order through the life that matters for money:

  checkout → signed webhook confirms it → the redelivery and the customer's own
  signed return are both no-ops → an admin partial refund, whose refund callback
  is recognised as already booked → a refund retried after its transaction
  rolled back is *not* sent twice → a refund made on Paymob's dashboard blocks a
  new one until it is reconciled, and its callback books it.

Plus the two recovery paths Paymob's undocumented retry policy makes necessary:
a return whose apply fails must not swallow the webhook, and a payment whose
webhook never arrives is found by the reconcile sweep — once.

Runs only when `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)

HMAC_SECRET = "paymob_it_secret"
CARD_ID = 5100001
WEBHOOK = "/api/v1/payments/webhooks/paymob"
RETURN = "/api/v1/payments/paymob/return"


class FakePaymob:
    """Paymob's documented endpoints, in memory."""

    def __init__(self):
        self.next_order = 880000 + (uuid.uuid4().int % 10000) * 10
        self.next_txn = 990000 + (uuid.uuid4().int % 10000) * 10
        self.intentions: dict[int, dict] = {}
        self.txns: dict[int, dict] = {}
        self.refund_posts: list[dict] = []

    def pay(self, paymob_order: int, *, success: bool = True) -> dict:
        """The customer pays on the hosted page: a transaction exists."""
        self.next_txn += 1
        intention = self.intentions[paymob_order]
        txn = {
            "id": self.next_txn,
            "amount_cents": intention["amount"],
            "created_at": "2026-09-25T10:00:00.000000",
            "currency": "AED",
            "error_occured": False,
            "has_parent_transaction": False,
            "integration_id": CARD_ID,
            "is_3d_secure": True,
            "is_auth": False,
            "is_capture": False,
            "is_refunded": False,
            "is_standalone_payment": True,
            "is_voided": False,
            "order": {"id": paymob_order},
            "owner": 42,
            "pending": False,
            "source_data": {"pan": "2346", "sub_type": "MasterCard", "type": "card"},
            "success": success,
            "refunded_amount_cents": 0,
            "data": {
                "message": "Approved" if success else "Do not honour",
                "acq_response_code": "00" if success else "05",
            },
        }
        self.txns[txn["id"]] = txn
        intention["latest_txn"] = txn["id"]
        return txn

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/tokens":
            return httpx.Response(201, json={"token": "bearer"})
        if path == "/v1/intention/":
            body = json.loads(request.content)
            self.next_order += 1
            self.intentions[self.next_order] = {"amount": body["amount"], "body": body}
            return httpx.Response(
                201,
                json={
                    "id": f"pi_test_{self.next_order}",
                    "intention_order_id": self.next_order,
                    "client_secret": f"csk_{self.next_order}",
                    "status": "intended",
                },
            )
        if path.startswith("/api/acceptance/transactions/"):
            txn = self.txns[int(path.rsplit("/", 1)[1])]
            return httpx.Response(200, json=txn)
        if path == "/api/acceptance/void_refund/refund":
            body = json.loads(request.content)
            self.refund_posts.append(body)
            txn = self.txns[body["transaction_id"]]
            txn["refunded_amount_cents"] += body["amount_cents"]
            txn["is_refunded"] = True
            return httpx.Response(
                200,
                json={
                    "id": self.next_txn + 500,
                    "success": True,
                    "pending": False,
                    "amount_cents": body["amount_cents"],
                    "is_refund": True,
                },
            )
        if path == "/api/ecommerce/orders/transaction_inquiry":
            paymob_order = int(json.loads(request.content)["order_id"])
            latest = self.intentions.get(paymob_order, {}).get("latest_txn")
            return httpx.Response(200, json=self.txns[latest] if latest else {})
        raise AssertionError(f"unexpected Paymob call {request.method} {path}")


def _sign(obj: dict) -> str:
    from app.services.providers import paymob_provider as pm

    values = [pm._signed_value(obj, f, flat=False) for f in pm._TRANSACTION_HMAC_FIELDS]
    return pm._expected_hmac(values, HMAC_SECRET)


def _redirect_query(obj: dict) -> dict[str, str]:
    from app.services.providers import paymob_provider as pm

    flat = {
        k: ("true" if v is True else "false" if v is False else str(v))
        for k, v in obj.items()
        if not isinstance(v, dict)
    }
    flat["order"] = str(obj["order"]["id"])
    for k, v in obj["source_data"].items():
        flat[f"source_data.{k}"] = str(v)
    values = [pm._signed_value(flat, f, flat=True) for f in pm._TRANSACTION_HMAC_FIELDS]
    flat["hmac"] = pm._expected_hmac(values, HMAC_SECRET)
    return flat


@pytest.fixture
async def world(monkeypatch):
    """A branch, an order, Paymob routed first, and the fake behind it."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import delete, update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.deps import get_db
    from app.main import app
    from app.models.branch import Branch
    from app.models.inventory import Warehouse
    from app.models.order import Order, OrderStatusEnum
    from app.models.payment_gateway import PaymentGateway
    from app.models.payment_transaction import PaymentTransaction
    from app.models.pos_order import OrderSourceEnum
    from app.models.webhook_event import WebhookEvent
    from app.services.delivery import arrival_service
    from app.services.inventory import source_event_service
    from app.services.payments import payment_service
    from app.services.providers import paymob_provider as pm

    for name, value in {
        "PAYMOB_ENABLED": True,
        "PAYMOB_SECRET_KEY": "sk_test_it",
        "PAYMOB_PUBLIC_KEY": "pk_test_it",
        "PAYMOB_API_KEY": "api_it",
        "PAYMOB_HMAC_SECRET": HMAC_SECRET,
        "PAYMOB_CARD_INTEGRATION_ID": CARD_ID,
        "PAYMOB_CALLBACK_BASE_URL": "https://api.example.test",
        "PAYMOB_API_URL": "https://uae.paymob.test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    pm._bearer.update(token=None, expires=0.0)

    fake = FakePaymob()
    monkeypatch.setattr(
        pm.PaymobProvider,
        "_client",
        staticmethod(
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        ),
    )

    emails = SimpleNamespace(
        confirmation=AsyncMock(),
        owner=AsyncMock(),
        failed=AsyncMock(),
        refund=AsyncMock(),
    )
    es = payment_service.email_service
    monkeypatch.setattr(es, "send_order_confirmation", emails.confirmation)
    monkeypatch.setattr(es, "send_owner_order_notification", emails.owner)
    monkeypatch.setattr(es, "send_payment_failed", emails.failed)
    monkeypatch.setattr(es, "send_refund_notification", emails.refund)
    monkeypatch.setattr(
        payment_service.order_service, "publish_to_register", AsyncMock()
    )
    monkeypatch.setattr(arrival_service, "schedule", AsyncMock(return_value=None))
    monkeypatch.setattr(
        source_event_service, "accept_order", AsyncMock(return_value=None)
    )
    # The full-refund status move's own consequences (restock, promo, courier)
    # reach far outside the payment tables and are covered elsewhere.
    from app.services.orders import order_lifecycle

    real_transition = order_lifecycle.transition

    async def transition(db, order, new, **kw):
        if new == OrderStatusEnum.CANCELLED:
            return True
        return await real_transition(db, order, new, **kw)

    monkeypatch.setattr(order_lifecycle, "transition", transition)

    engine = create_async_engine(DATABASE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    marker = f"PM-{uuid.uuid4().hex[:8]}"

    async with maker() as s:
        branch = Branch(name=marker, reference=marker)
        s.add(branch)
        await s.flush()
        s.add(Warehouse(branch_id=branch.id, name=marker, is_default=True))
        prior = (
            (
                await s.execute(
                    PaymentGateway.__table__.select().where(
                        PaymentGateway.code == "paymob"
                    )
                )
            )
            .mappings()
            .first()
        )
        await s.execute(
            update(PaymentGateway)
            .where(PaymentGateway.code == "paymob")
            .values(is_active=True, priority=0)
        )
        await s.commit()

    def make_order(**over):
        base = dict(
            order_number=f"{marker}-{uuid.uuid4().hex[:4]}",
            email="c@example.com",
            locale="en",
            delivery_method="pickup",
            order_type="pickup",
            status=OrderStatusEnum.CREATED,
            source=OrderSourceEnum.ONLINE.value,
            branch_id=branch.id,
            payment_method="card",
            subtotal=Decimal("125"),
            total=Decimal("125"),
            vat_amount=Decimal("0"),
            total_excl_vat=Decimal("125"),
            vat_rate=Decimal("0"),
            discount_amount=Decimal("0"),
            customer_name="Sara Ali",
            customer_phone="+971501234567",
        )
        base.update(over)
        return Order(**base)

    async def override_get_db():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    created: list = []

    async def new_order(**over):
        order = make_order(**over)
        async with maker() as s:
            s.add(order)
            await s.commit()
        created.append(order.id)
        return order

    async def load(order_id):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async with maker() as s:
            return (
                await s.execute(
                    select(Order)
                    .options(
                        selectinload(Order.items),
                        selectinload(Order.payment_transactions),
                    )
                    .where(Order.id == order_id)
                )
            ).scalar_one()

    try:
        yield SimpleNamespace(
            fake=fake,
            client=client,
            maker=maker,
            emails=emails,
            new_order=new_order,
            load=load,
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        async with maker() as s:
            await s.execute(
                delete(WebhookEvent).where(WebhookEvent.event_id.like("paymob:txn:%"))
            )
            for order_id in created:
                await s.execute(
                    delete(PaymentTransaction).where(
                        PaymentTransaction.order_id == order_id
                    )
                )
                await s.execute(delete(Order).where(Order.id == order_id))
            await s.execute(delete(Warehouse).where(Warehouse.branch_id == branch.id))
            await s.execute(delete(Branch).where(Branch.id == branch.id))
            await s.execute(
                update(PaymentGateway)
                .where(PaymentGateway.code == "paymob")
                .values(is_active=prior["is_active"], priority=prior["priority"])
            )
            await s.commit()
        await engine.dispose()


async def _checkout(world, order):
    from app.services.payments import payment_service

    async with world.maker() as s:
        result = await payment_service.create_session(
            s, order.order_number, "card", user_id=None, admin=True
        )
        await s.commit()
    assert result["provider"] == "paymob"
    return int(result["session_id"].removeprefix("ord_"))


async def _post(world, obj: dict):
    return await world.client.post(
        f"{WEBHOOK}?hmac={_sign(obj)}",
        content=json.dumps({"type": "TRANSACTION", "obj": obj}),
        headers={"content-type": "application/json"},
    )


async def _admin_refund(world, order_id, amount: str):
    from app.services.payments import payment_service

    async with world.maker() as s:
        order = await world.load(order_id)
        order = await s.merge(order)
        result = await payment_service.issue_admin_refund(
            s, order, amount=Decimal(amount)
        )
        await s.commit()
    return result


async def test_the_whole_life_of_a_paymob_order(world):
    from app.models.order import OrderStatusEnum

    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    intention = world.fake.intentions[paymob_order]["body"]
    assert intention["amount"] == 12500

    # ── the customer pays; the signed webhook confirms ──────────────────────
    txn = world.fake.pay(paymob_order)
    response = await _post(world, txn)
    assert response.status_code == 200, response.text
    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CONFIRMED
    settled = [t for t in stored.payment_transactions if t.is_settled]
    assert [t.payment_id for t in settled] == [f"txn_{txn['id']}"]
    assert world.emails.confirmation.await_count == 1

    # ── a redelivery, and the customer's signed return: both no-ops ─────────
    assert (await _post(world, txn)).json().get("duplicate") is True
    back = await world.client.get(
        RETURN, params=_redirect_query(txn), follow_redirects=False
    )
    assert back.status_code == 303
    assert "/checkout/confirmation" in back.headers["location"]
    assert f"order_number={order.order_number}" in back.headers["location"]
    assert world.emails.confirmation.await_count == 1, "one payment, one email"

    # ── an admin partial refund; its callback is an acknowledgement ─────────
    await _admin_refund(world, order.id, "30.00")
    assert world.fake.refund_posts[-1] == {
        "transaction_id": txn["id"],
        "amount_cents": 3000,
    }
    callback = dict(world.fake.txns[txn["id"]])
    assert (await _post(world, callback)).status_code == 200
    stored = await world.load(order.id)
    assert Decimal(str(stored.refunded_amount)) == Decimal("30.00"), "booked once"
    assert world.emails.refund.await_count == 0, "a partial is not the whole order"

    # ── a refund whose transaction rolled back after Paymob paid it ─────────
    from app.services.payments import payment_service

    async with world.maker() as s:
        loaded = await s.merge(await world.load(order.id))
        await payment_service.issue_admin_refund(s, loaded, amount=Decimal("20.00"))
        await s.rollback()  # the crash: money moved, our record did not
    posts_before = len(world.fake.refund_posts)
    await _admin_refund(world, order.id, "20.00")  # the retry
    assert len(world.fake.refund_posts) == posts_before, (
        "the customer is not refunded twice"
    )
    assert world.fake.txns[txn["id"]]["refunded_amount_cents"] == 5000
    stored = await world.load(order.id)
    assert Decimal(str(stored.refunded_amount)) == Decimal("50.00")

    # ── a refund made on Paymob's dashboard, callback not yet landed ────────
    world.fake.txns[txn["id"]]["refunded_amount_cents"] += 1000
    from app.core.exceptions import BadRequestError

    # (A different amount from the dashboard's: an unexplained refund of
    # exactly the requested amount is indistinguishable from this request's own
    # rolled-back attempt, and is booked without re-sending — never overpaying.)
    with pytest.raises(BadRequestError, match="Reconcile"):
        await _admin_refund(world, order.id, "15.00")
    # …and when its callback does land, it is booked from Paymob's own figure.
    assert (await _post(world, dict(world.fake.txns[txn["id"]]))).status_code == 200
    stored = await world.load(order.id)
    assert Decimal(str(stored.refunded_amount)) == Decimal("60.00")


async def test_a_forged_callback_touches_nothing(world):
    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    txn = world.fake.pay(paymob_order)
    response = await world.client.post(
        f"{WEBHOOK}?hmac={'0' * 128}",
        content=json.dumps({"type": "TRANSACTION", "obj": txn}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert (await world.load(order.id)).status.value == "created"


async def test_a_decline_then_a_retry_on_the_same_page_refunds_the_right_transaction(
    world,
):
    from app.models.order import OrderStatusEnum

    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    declined = world.fake.pay(paymob_order, success=False)
    assert (await _post(world, declined)).status_code == 200
    assert (await world.load(order.id)).status == OrderStatusEnum.PAYMENT_FAILED
    assert world.emails.failed.await_count == 1

    paid = world.fake.pay(paymob_order)  # same intention, new transaction
    assert (await _post(world, paid)).status_code == 200
    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CONFIRMED

    await _admin_refund(world, order.id, "25.00")
    assert world.fake.refund_posts[-1]["transaction_id"] == paid["id"], (
        "the refund must go to the transaction that took the money"
    )


async def test_a_return_that_cannot_be_applied_does_not_swallow_the_webhook(
    world, monkeypatch
):
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_service

    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    txn = world.fake.pay(paymob_order)

    real_apply = payment_service._apply_event

    async def boom(*a, **k):
        raise RuntimeError("database blip")

    monkeypatch.setattr(payment_service, "_apply_event", boom)
    back = await world.client.get(
        RETURN, params=_redirect_query(txn), follow_redirects=False
    )
    # A verified success still goes to the confirmation page — never back to pay.
    assert (
        back.status_code == 303 and "/checkout/confirmation" in back.headers["location"]
    )
    assert (await world.load(order.id)).status == OrderStatusEnum.CREATED

    monkeypatch.setattr(payment_service, "_apply_event", real_apply)
    response = await _post(world, txn)
    assert response.json().get("duplicate") is not True, (
        "the failed return left no dedup row"
    )
    assert (await world.load(order.id)).status == OrderStatusEnum.CONFIRMED


async def test_an_unsigned_return_goes_to_checkout_and_touches_nothing(world):
    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    query = _redirect_query(world.fake.pay(paymob_order))
    query["hmac"] = "f" * 128
    back = await world.client.get(RETURN, params=query, follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"].endswith("/checkout")
    assert (await world.load(order.id)).status.value == "created"


async def test_a_payment_whose_webhook_never_came_is_reconciled_once(
    world, monkeypatch
):
    from datetime import timedelta

    from app.models.base import utcnow
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_reconcile_service as prs

    order = await world.new_order(created_at=utcnow() - timedelta(minutes=30))
    paymob_order = await _checkout(world, order)
    txn = world.fake.pay(paymob_order)  # paid — and no webhook, ever

    @asynccontextmanager
    async def _always_mine(*a, **k):
        yield True

    monkeypatch.setattr(prs.advisory_lock, "held", _always_mine)
    monkeypatch.setattr(prs, "AsyncSessionFactory", world.maker)

    assert await prs.sweep_once() == 1
    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CONFIRMED
    assert world.emails.confirmation.await_count == 1

    # The webhook finally arrives: same event id, nothing happens twice.
    assert (await _post(world, txn)).json().get("duplicate") is True
    assert await prs.sweep_once() == 0
    assert world.emails.confirmation.await_count == 1


async def test_the_expiry_sweep_will_not_cancel_an_order_its_gateway_says_is_paid(
    world,
):
    from datetime import timedelta

    from app.models.base import utcnow
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_service

    order = await world.new_order(created_at=utcnow() - timedelta(hours=49))
    paymob_order = await _checkout(world, order)
    world.fake.pay(paymob_order)

    async with world.maker() as s:
        cancelled = await payment_service.expire_stale_checkouts(s)
        await s.commit()
    assert order.order_number not in cancelled
    assert (await world.load(order.id)).status == OrderStatusEnum.CREATED


async def test_a_second_charge_through_apple_pay_is_given_back_and_only_it(
    world, monkeypatch
):
    """
    The hosted page and the Apple Pay button are two intentions on one order.
    If both are paid, the second is a duplicate: refunded in full, against its
    own transaction, and its refund callback never reads as the order refunded.
    """
    from app.core.config import settings
    from app.models.order import OrderStatusEnum
    from app.services.payments import apple_pay_service

    monkeypatch.setattr(settings, "PAYMOB_APPLE_PAY_INTEGRATION_ID", CARD_ID + 1)
    order = await world.new_order()
    hosted = await _checkout(world, order)

    user = SimpleNamespace(id=None, is_admin=True, email="admin@example.com")
    async with world.maker() as s:
        session = await apple_pay_service.create_paymob_session(
            s, order.order_number, user
        )
        await s.commit()
    assert session["public_key"] == "pk_test_it"
    apple = max(world.fake.intentions)
    assert world.fake.intentions[apple]["body"]["payment_methods"] == [CARD_ID + 1]

    first = world.fake.pay(hosted)
    assert (await _post(world, first)).status_code == 200
    second = world.fake.pay(apple)
    second["integration_id"] = CARD_ID + 1
    assert (await _post(world, second)).status_code == 200

    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CONFIRMED
    assert world.fake.refund_posts == [
        {"transaction_id": second["id"], "amount_cents": 12500}
    ], "the duplicate — and only the duplicate — goes back"
    assert Decimal(str(stored.refunded_amount or 0)) == Decimal("0")
    assert stored.payment_id == f"txn_{first['id']}"

    # Paymob then reports the duplicate's refund. It is not the order's refund.
    assert (await _post(world, dict(world.fake.txns[second["id"]]))).status_code == 200
    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CONFIRMED
    assert Decimal(str(stored.refunded_amount or 0)) == Decimal("0")
    assert world.emails.refund.await_count == 0


async def test_two_successes_on_one_intention_keep_the_first_and_return_the_second(
    world,
):
    """
    One Paymob intention can carry more than one transaction. If two succeed,
    the first is the payment and the second is a duplicate — refunded against
    its own transaction, on its own row. The row of the payment that is kept
    must stay settled, so a later cancellation can still refund it.
    """
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_service

    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    first = world.fake.pay(paymob_order)
    assert (await _post(world, first)).status_code == 200
    second = world.fake.pay(paymob_order)  # same intention, second success
    response = await _post(world, second)
    assert response.status_code == 200, response.text

    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CONFIRMED
    assert world.fake.refund_posts == [
        {"transaction_id": second["id"], "amount_cents": 12500}
    ]
    by_payment = {t.payment_id: t for t in stored.payment_transactions}
    assert by_payment[f"txn_{first['id']}"].is_settled, "the kept payment stays paid"
    assert by_payment[f"txn_{second['id']}"].status == "refunded"
    assert payment_service._is_paid(stored)

    # Paymob reports the duplicate's refund: the kept payment is untouched.
    assert (await _post(world, dict(world.fake.txns[second["id"]]))).status_code == 200
    stored = await world.load(order.id)
    assert {t.payment_id: t for t in stored.payment_transactions}[
        f"txn_{first['id']}"
    ].is_settled
    assert Decimal(str(stored.refunded_amount or 0)) == Decimal("0")

    # And a refund of the order still goes to the payment that was kept.
    async with world.maker() as s:
        loaded = await s.merge(await world.load(order.id))
        refunded = await payment_service.refund_order(s, loaded)
        await s.commit()
    assert refunded == Decimal("125.00")
    assert world.fake.refund_posts[-1] == {
        "transaction_id": first["id"],
        "amount_cents": 12500,
    }


# ── from the money-path adversary review: each was reproduced, then fixed ─────


async def test_a_paid_retry_after_a_decline_is_reconciled_not_cancelled(
    world, monkeypatch
):
    """Decline, then a successful retry on the same intention whose webhook and
    return are both lost. The attempt reads `failed`, and the sweeps used to ask
    only about `pending` ones — so the 48h sweep cancelled a paid order."""
    from datetime import timedelta

    from app.models.base import utcnow
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_reconcile_service as prs

    order = await world.new_order(created_at=utcnow() - timedelta(minutes=30))
    paymob_order = await _checkout(world, order)
    assert (
        await _post(world, world.fake.pay(paymob_order, success=False))
    ).status_code == 200
    world.fake.pay(paymob_order)  # paid on retry; nothing tells us

    @asynccontextmanager
    async def _mine(*a, **k):
        yield True

    monkeypatch.setattr(prs.advisory_lock, "held", _mine)
    monkeypatch.setattr(prs, "AsyncSessionFactory", world.maker)
    assert await prs.sweep_once() == 1
    assert (await world.load(order.id)).status == OrderStatusEnum.CONFIRMED


async def test_the_expiry_sweep_will_not_cancel_a_paid_retry_after_a_decline(world):
    from datetime import timedelta

    from app.models.base import utcnow
    from app.services.payments import payment_service

    order = await world.new_order(created_at=utcnow() - timedelta(hours=49))
    paymob_order = await _checkout(world, order)
    assert (
        await _post(world, world.fake.pay(paymob_order, success=False))
    ).status_code == 200
    world.fake.pay(paymob_order)
    async with world.maker() as s:
        cancelled = await payment_service.expire_stale_checkouts(s)
        await s.commit()
    assert order.order_number not in cancelled


async def test_an_abandoned_checkout_paymob_has_no_transaction_for_still_expires(
    world, monkeypatch
):
    """Inquiry answering 4xx for an order nobody paid on is 'no payment', not
    'maybe paid' — or no abandoned Paymob checkout would ever be cancelled."""
    from datetime import timedelta

    from app.models.base import utcnow
    from app.services.payments import payment_service
    from app.services.providers import paymob_provider as pm

    order = await world.new_order(created_at=utcnow() - timedelta(hours=49))
    await _checkout(world, order)

    def handler(request):
        if request.url.path == "/api/ecommerce/orders/transaction_inquiry":
            return httpx.Response(404, json={"detail": "Not found."})
        return world.fake.handler(request)

    monkeypatch.setattr(
        pm.PaymobProvider,
        "_client",
        staticmethod(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))),
    )
    async with world.maker() as s:
        cancelled = await payment_service.expire_stale_checkouts(s)
        await s.commit()
    assert order.order_number in cancelled


async def test_an_underpaid_order_is_parked_for_a_person(world, monkeypatch):
    """Money taken, order not confirmed. Never cancelled by the sweep, and the
    gateway is not asked about it again every tick."""
    from datetime import timedelta

    from app.models.base import utcnow
    from app.models.order import OrderStatusEnum
    from app.services.payments import payment_service
    from app.services.providers import paymob_provider as pm

    order = await world.new_order(created_at=utcnow() - timedelta(hours=49))
    paymob_order = await _checkout(world, order)
    txn = world.fake.pay(paymob_order)
    txn["amount_cents"] = 100  # AED 1 of 125
    assert (await _post(world, txn)).status_code == 200

    stored = await world.load(order.id)
    assert stored.status == OrderStatusEnum.CREATED
    attempt = next(t for t in stored.payment_transactions if t.session_id)
    assert attempt.status == "pending" and attempt.error_code == "underpaid"
    assert payment_service._is_paid(stored) is False

    asked = []
    real = pm.PaymobProvider.fetch_outcome

    async def counting(self, a):
        asked.append(a)
        return await real(self, a)

    monkeypatch.setattr(pm.PaymobProvider, "fetch_outcome", counting)
    async with world.maker() as s:
        cancelled = await payment_service.expire_stale_checkouts(s)
        await s.commit()
    assert order.order_number not in cancelled
    assert asked == [], "parked, not re-queried"


async def test_a_cancel_refund_and_its_fast_callback_do_not_deadlock(
    world, monkeypatch
):
    """The cancellation holds the order row while Paymob refunds; Paymob's
    refund callback lands before that commits. The callback used to take the
    attempt row, then wait on the order — deadlocking with the cancel."""
    import asyncio

    from sqlalchemy import select, update
    from sqlalchemy.orm import selectinload

    from app.models.order import Order
    from app.services.payments import payment_service
    from app.services.providers import paymob_provider as pm

    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    txn = world.fake.pay(paymob_order)
    assert (await _post(world, txn)).status_code == 200

    pending: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        response = world.fake.handler(request)
        if request.url.path == "/api/acceptance/void_refund/refund":
            pending["callback"] = asyncio.create_task(
                _post(world, dict(world.fake.txns[txn["id"]]))
            )
            await asyncio.sleep(0.8)  # let it reach the lock
        return response

    monkeypatch.setattr(
        pm.PaymobProvider,
        "_client",
        staticmethod(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))),
    )

    async with world.maker() as s:
        loaded = (
            await s.execute(
                select(Order)
                .options(
                    selectinload(Order.payment_transactions), selectinload(Order.items)
                )
                .where(Order.id == order.id)
            )
        ).scalar_one()
        # What the cancel's own status write does first: hold the order row.
        await s.execute(
            update(Order).where(Order.id == order.id).values(admin_notes="x")
        )
        assert await payment_service.refund_order(s, loaded) == Decimal("125.00")
        await s.commit()  # a deadlock would abort one of the two here

    callback = await pending["callback"]
    assert callback.status_code == 200, callback.text
    stored = await world.load(order.id)
    assert Decimal(str(stored.refunded_amount)) == Decimal("125.00")
    assert len(world.fake.refund_posts) == 1


async def test_a_refund_reported_on_the_child_transaction_is_booked_once(world):
    """Paymob may report a dashboard refund on the refund (child) transaction
    rather than the parent. It is resolved through the parent — read back from
    Paymob — and a parent callback for the same refund is then a duplicate."""
    order = await world.new_order()
    paymob_order = await _checkout(world, order)
    txn = world.fake.pay(paymob_order)
    assert (await _post(world, txn)).status_code == 200

    # A refund made on Paymob's dashboard: parent updated, child transaction.
    parent = world.fake.txns[txn["id"]]
    parent["refunded_amount_cents"] = 2500
    parent["is_refunded"] = True
    world.fake.next_txn += 1
    child = {
        **parent,
        "id": world.fake.next_txn,
        "amount_cents": 2500,
        "has_parent_transaction": True,
        "is_refunded": False,
        "parent_transaction": txn["id"],
    }
    world.fake.txns[child["id"]] = child

    assert (await _post(world, child)).status_code == 200
    stored = await world.load(order.id)
    assert Decimal(str(stored.refunded_amount)) == Decimal("25.00")

    assert (await _post(world, dict(parent))).json().get("duplicate") is True
    assert Decimal(str((await world.load(order.id)).refunded_amount)) == Decimal(
        "25.00"
    )
