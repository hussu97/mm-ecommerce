"""The console cannot be left with nobody who can manage user accounts (F-ADM-12).

An admin editing a role must not strip the last `admin.users.manage` authority
from every role that has users — that would lock everyone out of user admin.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.staff import _would_orphan_user_management
from app.models import Role, User
from app.schemas.pos import RoleUpdate

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-last-admin"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _role(db, *, manages, users):
    role = Role(
        name=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        permissions=["admin.users.manage"] if manages else ["orders.read"],
    )
    db.add(role)
    await db.flush()
    for _ in range(users):
        db.add(User(email=f"{MARKER}-{uuid.uuid4().hex[:10]}@x.com", role_id=role.id))
    await db.flush()
    return role


async def test_stripping_the_last_user_management_role_is_refused(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    created: list[uuid.UUID] = []
    try:
        async with Session() as db:
            # The only role with users that can manage users.
            only = await _role(db, manages=True, users=1)
            created.append(only.id)
            await db.commit()

        async with Session() as db:
            only = await db.get(Role, created[0])
            strip = RoleUpdate(permissions=["orders.read"])
            assert await _would_orphan_user_management(db, only, strip) is True

            # A role with the authority but NO users is not the guard's concern —
            # nobody is signed into it, so removing it locks no one out.
            only.permissions = ["admin.users.manage"]
            unused = await _role(db, manages=True, users=0)
            created.append(unused.id)
            await db.commit()
            assert await _would_orphan_user_management(db, unused, strip) is False

        async with Session() as db:
            # A SECOND role with users that can manage users → now the first can
            # be stripped safely, because the second still covers it.
            other = await _role(db, manages=True, users=1)
            created.append(other.id)
            await db.commit()
            only = await db.get(Role, created[0])
            assert await _would_orphan_user_management(db, only, strip) is False
    finally:
        async with Session() as db:
            await db.execute(User.__table__.delete().where(User.role_id.in_(created)))
            await db.execute(Role.__table__.delete().where(Role.id.in_(created)))
            await db.commit()
