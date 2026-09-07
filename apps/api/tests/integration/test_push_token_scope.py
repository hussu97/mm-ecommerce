"""
Push-token registration and device pairing are scoped and guarded (F-POS-13/30).

Push-token register/revoke used to gate only on `get_current_active_user` — any
active account, a storefront customer's included — with a client-chosen branch,
so a customer could register or silence a push token for any branch (the payloads
carry staff name and phone). They now require `pos.register.access` and branch
membership.

Device pairing had no rate limit, no audit row, and was accepted on the public
storefront host. It now refuses off the register host, writes an audit row, and
is rate limited.

Real Postgres: these move rows (branch membership, device tokens, audit trail)
across sessions, which a mocked DB cannot express.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.devices import PAIRING_CODE_TTL
from app.core.deps import get_current_active_user, get_db
from app.main import app as web_app
from app.models.audit_log import AuditLog
from app.models.base import utcnow
from app.models.device import Device
from app.models.device_push_token import DevicePushToken
from app.models.inventory import Warehouse
from app.models.role import Role, UserBranch
from app.models.user import User
from app.pos_main import app as pos_app

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@pytest.fixture
async def engine():
    engine = create_async_engine(_DB_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def Session(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _committing_get_db(Session):
    async def override():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return override


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def branch(Session):
    """A branch with its mandatory default stock container; cleaned up after."""
    tag = uuid.uuid4().hex[:10]
    async with Session() as s:
        from app.models.branch import Branch

        b = Branch(name=f"pushtest {tag}", reference=f"PT-{tag}")
        s.add(b)
        await s.flush()
        s.add(Warehouse(branch_id=b.id, name="Default stock", is_default=True))
        await s.commit()
        branch_id = b.id
    yield branch_id
    async with Session() as s:
        from app.models.branch import Branch

        await s.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await s.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await s.commit()


async def _make_staff(Session, branch_id) -> uuid.UUID:
    async with Session() as s:
        u = User(
            email=f"pushstaff-{uuid.uuid4().hex}@example.com",
            is_active=True,
            is_staff=True,
        )
        s.add(u)
        await s.flush()
        s.add(UserBranch(user_id=u.id, branch_id=branch_id))
        await s.commit()
        return u.id


async def _cleanup_user(Session, user_id):
    async with Session() as s:
        await s.execute(
            DevicePushToken.__table__.delete().where(DevicePushToken.user_id == user_id)
        )
        await s.execute(
            UserBranch.__table__.delete().where(UserBranch.user_id == user_id)
        )
        await s.execute(User.__table__.delete().where(User.id == user_id))
        await s.commit()


def _staff_principal(user_id):
    """An in-memory register-permitted staff user with the given id."""
    return User(
        id=user_id,
        email="principal@example.com",
        is_active=True,
        is_staff=True,
        is_admin=False,
        role=Role(name="cashier", permissions=["pos.register.access"]),
    )


def _customer_principal():
    return User(
        id=uuid.uuid4(),
        email="customer@example.com",
        is_active=True,
        is_admin=False,
        role=None,
    )


class TestPushTokenScope:
    async def test_customer_cannot_register_a_push_token(self, Session, branch):
        web_app.dependency_overrides[get_db] = _committing_get_db(Session)
        web_app.dependency_overrides[get_current_active_user] = _customer_principal
        try:
            async with _client(web_app) as ac:
                resp = await ac.post(
                    "/api/v1/devices/push-token",
                    json={
                        "token": "a" * 64,
                        "bundle_id": "com.meltingmoments.pos",
                        "branch_id": str(branch),
                    },
                )
            assert resp.status_code == 403
        finally:
            web_app.dependency_overrides.clear()

    async def test_staff_cannot_register_for_a_branch_they_are_not_in(
        self, Session, branch
    ):
        staff_id = await _make_staff(Session, branch)
        other_branch = uuid.uuid4()  # a branch the staff has no membership of
        web_app.dependency_overrides[get_db] = _committing_get_db(Session)
        web_app.dependency_overrides[get_current_active_user] = lambda: (
            _staff_principal(staff_id)
        )
        try:
            async with _client(web_app) as ac:
                resp = await ac.post(
                    "/api/v1/devices/push-token",
                    json={
                        "token": "b" * 64,
                        "bundle_id": "com.meltingmoments.pos",
                        "branch_id": str(other_branch),
                    },
                )
            assert resp.status_code == 403
        finally:
            web_app.dependency_overrides.clear()
            await _cleanup_user(Session, staff_id)

    async def test_staff_can_register_for_their_own_branch(self, Session, branch):
        staff_id = await _make_staff(Session, branch)
        web_app.dependency_overrides[get_db] = _committing_get_db(Session)
        web_app.dependency_overrides[get_current_active_user] = lambda: (
            _staff_principal(staff_id)
        )
        try:
            async with _client(web_app) as ac:
                resp = await ac.post(
                    "/api/v1/devices/push-token",
                    json={
                        "token": "c" * 64,
                        "bundle_id": "com.meltingmoments.pos",
                        "branch_id": str(branch),
                    },
                )
            assert resp.status_code == 200
            async with Session() as s:
                row = (
                    await s.execute(
                        select(DevicePushToken).where(DevicePushToken.token == "c" * 64)
                    )
                ).scalar_one()
            assert row.branch_id == branch
        finally:
            web_app.dependency_overrides.clear()
            await _cleanup_user(Session, staff_id)

    async def test_revoke_is_scoped_to_the_branch_owner(self, Session, branch):
        # A token bound to `branch`; a staff member of a *different* branch must
        # not be able to revoke it.
        outsider_branch_id = None
        async with Session() as s:
            from app.models.branch import Branch

            other = Branch(name="other", reference=f"OT-{uuid.uuid4().hex[:8]}")
            s.add(other)
            await s.flush()
            s.add(Warehouse(branch_id=other.id, name="Default", is_default=True))
            token = DevicePushToken(
                token="d" * 64, bundle_id="com.meltingmoments.pos", branch_id=branch
            )
            s.add(token)
            await s.commit()
            outsider_branch_id = other.id

        outsider_id = await _make_staff(Session, outsider_branch_id)
        web_app.dependency_overrides[get_db] = _committing_get_db(Session)
        web_app.dependency_overrides[get_current_active_user] = lambda: (
            _staff_principal(outsider_id)
        )
        try:
            async with _client(web_app) as ac:
                resp = await ac.delete("/api/v1/devices/push-token/" + "d" * 64)
            assert resp.status_code == 403
            async with Session() as s:
                row = (
                    await s.execute(
                        select(DevicePushToken).where(DevicePushToken.token == "d" * 64)
                    )
                ).scalar_one()
            assert row.revoked_at is None, "an outsider must not revoke the token"
        finally:
            web_app.dependency_overrides.clear()
            await _cleanup_user(Session, outsider_id)
            async with Session() as s:
                from app.models.branch import Branch

                await s.execute(
                    DevicePushToken.__table__.delete().where(
                        DevicePushToken.token == "d" * 64
                    )
                )
                await s.execute(
                    Warehouse.__table__.delete().where(
                        Warehouse.branch_id == outsider_branch_id
                    )
                )
                await s.execute(
                    Branch.__table__.delete().where(Branch.id == outsider_branch_id)
                )
                await s.commit()


class TestDevicePairing:
    async def _make_device(self, Session, branch_id, code) -> uuid.UUID:
        async with Session() as s:
            d = Device(
                name="till 1",
                reference=f"DEV-{uuid.uuid4().hex[:8]}",
                type="cashier",
                branch_id=branch_id,
                status="available",
                pairing_code=code,
                pairing_code_expires_at=utcnow() + PAIRING_CODE_TTL,
            )
            s.add(d)
            await s.commit()
            return d.id

    async def _cleanup_device(self, Session, device_id):
        async with Session() as s:
            await s.execute(
                AuditLog.__table__.delete().where(AuditLog.entity_id == str(device_id))
            )
            await s.execute(Device.__table__.delete().where(Device.id == device_id))
            await s.commit()

    async def test_pairing_is_refused_on_the_storefront_host(self, Session, branch):
        device_id = await self._make_device(Session, branch, "PAIRWEB1")
        web_app.dependency_overrides[get_db] = _committing_get_db(Session)
        try:
            async with _client(web_app) as ac:
                resp = await ac.post(
                    "/api/v1/devices/pair", json={"pairing_code": "PAIRWEB1"}
                )
            # Refused before the code is even consulted: wrong host.
            assert resp.status_code == 401
            async with Session() as s:
                dev = await s.get(Device, device_id)
                assert dev.token_hash is None, "no token should have been minted"
        finally:
            web_app.dependency_overrides.clear()
            await self._cleanup_device(Session, device_id)

    async def test_pairing_on_the_register_host_succeeds_and_is_audited(
        self, Session, branch
    ):
        device_id = await self._make_device(Session, branch, "PAIRPOS2")
        pos_app.dependency_overrides[get_db] = _committing_get_db(Session)
        try:
            async with _client(pos_app) as ac:
                resp = await ac.post(
                    "/api/v1/devices/pair",
                    json={"pairing_code": "PAIRPOS2"},
                    headers={"X-Forwarded-For": f"10.0.0.{uuid.uuid4().int % 250 + 1}"},
                )
            assert resp.status_code == 200
            assert resp.json()["device_token"]
            async with Session() as s:
                audit = (
                    (
                        await s.execute(
                            select(AuditLog).where(AuditLog.entity_id == str(device_id))
                        )
                    )
                    .scalars()
                    .all()
                )
            assert audit, "pairing must leave an audit row"
            assert audit[0].entity_type == "device"
        finally:
            pos_app.dependency_overrides.clear()
            await self._cleanup_device(Session, device_id)

    async def test_pairing_is_rate_limited(self, Session, branch):
        device_id = await self._make_device(Session, branch, "PAIRRL33")
        pos_app.dependency_overrides[get_db] = _committing_get_db(Session)
        # A bucket of this test's own, so the count is not shared with others.
        ip = f"172.31.{uuid.uuid4().int % 250 + 1}.{uuid.uuid4().int % 250 + 1}"
        try:
            async with _client(pos_app) as ac:
                statuses = []
                for _ in range(12):
                    r = await ac.post(
                        "/api/v1/devices/pair",
                        json={"pairing_code": "NOPE9999"},  # wrong on purpose
                        headers={"X-Forwarded-For": ip},
                    )
                    statuses.append(r.status_code)
            # The limit is 10/min: the first ten reach the handler (401 bad code),
            # then the limiter takes over with 429.
            assert 429 in statuses, statuses
            assert statuses[:10] == [401] * 10, statuses
        finally:
            pos_app.dependency_overrides.clear()
            await self._cleanup_device(Session, device_id)
