"""
`POST /cart/merge` merges the caller's own session, not one named in the body.

The route took an arbitrary `session_id` in its request body and merged *that*
guest cart into the signed-in user's — absorbing its items and then deleting it.
So any authenticated caller who could name (or guess) another shopper's session
could empty their basket into their own. Every sibling cart route reads the
session from the `X-Session-Id` header; merge now does the same, so there is one
identity mechanism and the body cannot point it at a stranger's cart.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest

# ── the route reads the header, not the body ──────────────────────────────────


class _User:
    def __init__(self):
        self.id = uuid.uuid4()
        self.is_admin = False


@pytest.fixture
def signed_in():
    from app.core.deps import get_optional_user
    from app.main import app

    user = _User()

    async def override():
        return user

    app.dependency_overrides[get_optional_user] = override
    yield user
    app.dependency_overrides.pop(get_optional_user, None)


async def test_merge_uses_the_header_session_and_ignores_the_body(client, signed_in):
    from app.schemas.cart import CartResponse
    from app.services import cart_service

    result = CartResponse(id=uuid.uuid4(), user_id=signed_in.id, session_id=None)
    with patch.object(
        cart_service, "merge", new=AsyncMock(return_value=result)
    ) as merge:
        response = await client.post(
            "/api/v1/cart/merge",
            headers={"X-Session-Id": "sess_mine"},
            json={"session_id": "sess_someone_elses"},
        )

    assert response.status_code == 200
    # The header session is what gets merged — never the body's.
    merge.assert_awaited_once()
    assert merge.await_args.kwargs["guest_session_id"] == "sess_mine"
    assert merge.await_args.kwargs["user_id"] == signed_in.id


async def test_merge_with_no_header_merges_nothing_named(client, signed_in):
    from app.schemas.cart import CartResponse
    from app.services import cart_service

    result = CartResponse(id=uuid.uuid4(), user_id=signed_in.id, session_id=None)
    with patch.object(
        cart_service, "merge", new=AsyncMock(return_value=result)
    ) as merge:
        response = await client.post(
            "/api/v1/cart/merge", json={"session_id": "sess_someone_elses"}
        )

    assert response.status_code == 200
    assert merge.await_args.kwargs["guest_session_id"] is None


async def test_merge_requires_authentication(client):
    """No override here — an anonymous caller cannot merge anything."""
    response = await client.post(
        "/api/v1/cart/merge", headers={"X-Session-Id": "sess_x"}
    )
    assert response.status_code == 401


# ── against a real database: a stranger's cart survives ───────────────────────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
class TestAgainstRealCarts:
    @pytest.fixture
    async def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(DATABASE_URL)
        yield engine
        await engine.dispose()

    async def test_the_body_cannot_absorb_a_strangers_cart(self, engine):
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models.cart import Cart
        from app.models.user import User
        from app.services import cart_service

        tag = uuid.uuid4().hex[:10]
        victim_sid = f"sess_victim_{tag}"
        attacker_sid = f"sess_attacker_{tag}"
        Session = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with Session() as db:
                attacker = User(email=f"attacker-{tag}@example.com")
                db.add(attacker)
                db.add(Cart(session_id=victim_sid))
                db.add(Cart(session_id=attacker_sid))
                await db.commit()
                attacker_id = attacker.id

            # The attack: signed in, header carries the attacker's own session,
            # body names the victim's. The server merges the header session.
            async with Session() as db:
                await cart_service.merge(
                    db, guest_session_id=attacker_sid, user_id=attacker_id
                )
                await db.commit()

            async with Session() as db:
                victim = (
                    await db.execute(select(Cart).where(Cart.session_id == victim_sid))
                ).scalar_one_or_none()
                assert victim is not None, "the stranger's cart must be untouched"

                # The attacker's own guest cart was the one merged (and removed).
                own = (
                    await db.execute(
                        select(Cart).where(Cart.session_id == attacker_sid)
                    )
                ).scalar_one_or_none()
                assert own is None, "the caller's own session cart is what merges"

            async with Session() as db:
                await db.execute(delete(Cart).where(Cart.session_id == victim_sid))
                await db.execute(delete(Cart).where(Cart.user_id == attacker_id))
                await db.execute(delete(User).where(User.id == attacker_id))
                await db.commit()
        finally:
            await engine.dispose()
