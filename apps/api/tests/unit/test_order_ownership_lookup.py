"""
An order's email is matched case-insensitively (F-ORD-3).

`orders.email` was stored exactly as typed while every ownership check
lower-cased the value it compared against. So an order placed as `John@x.com`
was unreachable by its own customer: `/orders/{n}?email=John@x.com` and
`/orders/track` both 403'd or 404'd, because `John@x.com != john@x.com`.

The fix folds the address on write (schema + `_persist_order`), compares
case-insensitively on read, and a migration lower-cases the rows already stored.
These pin the read and write halves; the migration is validated up/down/up.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ForbiddenError
from app.services.orders import order_service

# ── write side: the schema folds the address ────────────────────────────────


def _order_create(**over):
    from app.models.order import DeliveryMethodEnum
    from app.schemas.order import OrderCreate

    base = dict(
        email="John@X.com",
        delivery_method=DeliveryMethodEnum.PICKUP,
        payment_method="cod",
    )
    return OrderCreate(**{**base, **over})


def test_ordercreate_lowercases_and_trims_the_email():
    assert _order_create(email="  John@X.com ").email == "john@x.com"


def test_ordercreate_still_refuses_a_missing_email():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _order_create(email="   ")


# ── read side: ownership is case-insensitive ────────────────────────────────


def _db_returning(order):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=order)
    db.execute = AsyncMock(return_value=result)
    return db


async def test_a_guest_reaches_their_order_despite_a_capitalised_email(monkeypatch):
    order = SimpleNamespace(email="john@x.com", user_id=None, order_number="MM-1")
    sentinel = object()

    async def fake_to_response(db, o):
        assert o is order
        return sentinel

    monkeypatch.setattr(order_service, "to_response", fake_to_response)

    got = await order_service.get_by_order_number(
        _db_returning(order), "MM-1", email="John@X.com"
    )
    assert got is sentinel


async def test_a_wrong_email_is_still_refused(monkeypatch):
    order = SimpleNamespace(email="john@x.com", user_id=None, order_number="MM-1")
    monkeypatch.setattr(order_service, "to_response", AsyncMock())

    with pytest.raises(ForbiddenError):
        await order_service.get_by_order_number(
            _db_returning(order), "MM-1", email="someone-else@x.com"
        )


# ── read side: the signed receipt token is accepted too (F-ORD-20) ───────────


async def test_a_receipt_token_proves_ownership_without_the_email(monkeypatch):
    """The confirmation page arrives with a token instead of the email, so the
    return URL never had to carry the address. The token alone opens the order."""
    from app.core import receipt_token

    order_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id, email="john@x.com", user_id=None, order_number="MM-1"
    )
    sentinel = object()

    async def fake_to_response(db, o):
        assert o is order
        return sentinel

    monkeypatch.setattr(order_service, "to_response", fake_to_response)

    got = await order_service.get_by_order_number(
        _db_returning(order), "MM-1", token=receipt_token.mint(order_id)
    )
    assert got is sentinel


async def test_the_email_path_still_works_alongside_the_token(monkeypatch):
    """Additive: links already in the wild carry the email and must keep opening
    the order even with no token."""
    order = SimpleNamespace(
        id=uuid.uuid4(), email="john@x.com", user_id=None, order_number="MM-1"
    )
    sentinel = object()

    async def fake_to_response(db, o):
        return sentinel

    monkeypatch.setattr(order_service, "to_response", fake_to_response)

    got = await order_service.get_by_order_number(
        _db_returning(order), "MM-1", email="John@X.com"
    )
    assert got is sentinel


async def test_a_token_for_a_different_order_is_refused(monkeypatch):
    """A token minted for some other order proves nothing about this one, and
    with no valid email either the lookup is refused rather than served."""
    from app.core import receipt_token

    order = SimpleNamespace(
        id=uuid.uuid4(), email="john@x.com", user_id=None, order_number="MM-1"
    )
    monkeypatch.setattr(order_service, "to_response", AsyncMock())

    with pytest.raises(ForbiddenError):
        await order_service.get_by_order_number(
            _db_returning(order), "MM-1", token=receipt_token.mint(uuid.uuid4())
        )


# ── the SQL predicate track/lookup uses (real Postgres) ─────────────────────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
async def test_func_lower_finds_a_stored_order_by_a_capitalised_email():
    """
    `/orders/track` selects with `func.lower(Order.email) == email.lower()`. This
    proves that predicate finds a stored (lower-cased) row when the caller types
    the address with capitals — the exact query the route runs.
    """
    from sqlalchemy import delete, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Branch, Order
    from app.models.inventory import Warehouse
    from app.models.order import DeliveryMethodEnum, OrderStatusEnum

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:12]
    try:
        async with Session() as db:
            branch = Branch(name=f"own-{tag}", reference=f"own-{tag}")
            db.add(branch)
            await db.flush()
            db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
            order = Order(
                order_number=f"OWN-{tag}",
                email="john@x.com",  # stored canonical, as creation now writes it
                source="online",
                branch_id=branch.id,
                status=OrderStatusEnum.CREATED,
                delivery_method=DeliveryMethodEnum.PICKUP,
                subtotal="10.00",
                total="10.00",
                delivery_fee="0.00",
            )
            db.add(order)
            await db.commit()
            order_id, branch_id = order.id, branch.id

        async with Session() as db:
            found = (
                await db.execute(
                    select(Order).where(
                        Order.order_number == f"OWN-{tag}",
                        func.lower(Order.email) == "John@X.com".strip().lower(),
                    )
                )
            ).scalar_one_or_none()
            assert found is not None, "a capitalised email must still find the row"

        async with Session() as db:
            await db.execute(delete(Order).where(Order.id == order_id))
            await db.execute(delete(Branch).where(Branch.id == branch_id))
            await db.commit()
    finally:
        await engine.dispose()
