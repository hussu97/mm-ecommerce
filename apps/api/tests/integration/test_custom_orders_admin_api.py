"""
The three custom-order admin writes round-trip without 500ing (F-ORD-2).

Every one of them ends by writing an audit-log row, and every one called
`audit_service.log_action(db, user=admin, action="create", ...)` — with a
positional-name the function does not accept (`user=` where it wants `admin=`)
and no `entity_label`, which `log_action` requires. Because keyword binding
happens before the function body's `try/except`, the `TypeError` was raised at
the *call*, never caught, and surfaced as a 500 on create, update and status —
so admins could not book, edit or move a single custom order from the console.

This drives the real routes through the ASGI app against a real Postgres (the
audit row is genuinely inserted), so a mock cannot paper over the binding error.
It SKIPs unless `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-cust-admin-"


class _Admin:
    """A staff user who holds every permission the routes ask for."""

    def __init__(self):
        self.id = uuid.uuid4()
        self.email = "pytest-custom-admin@example.com"
        self.is_admin = True

    def can(self, _permission: str) -> bool:
        return True


@pytest.fixture
async def maker():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def admin(maker):
    """The staff user, present in `users` so `custom_orders.created_by_id` (an
    FK) is satisfiable."""
    from app.models.user import User

    who = _Admin()
    async with maker() as db:
        db.add(User(id=who.id, email=who.email, is_admin=True, is_staff=True))
        await db.commit()
    yield who
    async with maker() as db:
        await db.execute(delete(User).where(User.id == who.id))
        await db.commit()


@pytest.fixture
async def client(maker, admin):
    """A client whose `get_db` commits like production's and whose staff user
    is our all-permissions admin."""
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user, get_db
    from app.main import app

    async def override_get_db():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_admin():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_admin
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def cleanup(maker, admin):
    yield
    from app.models.audit_log import AuditLog
    from app.models.custom_order import CustomOrder

    async with maker() as db:
        await db.execute(
            delete(CustomOrder).where(CustomOrder.customer_name.like(f"{MARKER}%"))
        )
        await db.execute(delete(AuditLog).where(AuditLog.admin_id == admin.id))
        await db.commit()


BASE = "/api/v1/admin/custom-orders"


async def _audit_count(maker, entity_id: str) -> int:
    from app.models.audit_log import AuditLog

    async with maker() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.entity_id == entity_id)
                )
            ).scalar()
            or 0
        )


async def test_create_update_and_status_all_round_trip(client, maker, admin):
    due = (date.today() + timedelta(days=30)).isoformat()

    # CREATE — this 500'd before the fix.
    created = await client.post(
        BASE,
        json={
            "due_date": due,
            "customer_name": f"{MARKER}aisha",
            "description": "Two-tier pistachio with gold leaf",
        },
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    # UPDATE — same call shape, same bug.
    updated = await client.put(
        f"{BASE}/{order_id}",
        json={"cake_message": "Happy birthday"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["cake_message"] == "Happy birthday"

    # STATUS — the third of the three.
    moved = await client.put(
        f"{BASE}/{order_id}/status",
        json={"status": "confirmed"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "confirmed"

    # All three audit rows landed — the call binds now, so nothing raised.
    assert await _audit_count(maker, order_id) == 3
