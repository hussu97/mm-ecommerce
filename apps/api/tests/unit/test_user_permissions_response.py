"""`UserResponse` carries the permission slugs and the super-admin flag the admin
console gates its nav on (F-ADM-7).

Pure model/schema assertions — no DB. `User.role` is `lazy="selectin"`, so
reading it in the properties never lazy-loads; here the role is set directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserResponse


def _user(**kw) -> User:
    base = dict(
        id=uuid.uuid4(),
        email="staff@example.com",
        phone=None,
        is_active=True,
        is_admin=False,
        is_guest=False,
        created_at=datetime.now(timezone.utc),
        role=None,
    )
    base.update(kw)
    return User(**base)


def test_a_console_admin_is_superadmin_with_no_enumerated_slugs():
    user = _user(is_admin=True)
    assert user.is_superadmin is True
    assert user.permissions == []
    dumped = UserResponse.model_validate(user)
    assert dumped.is_superadmin is True
    assert dumped.permissions == []


def test_a_super_admin_role_is_superadmin_too():
    user = _user(role=Role(name="Owner", permissions=[], is_super_admin=True))
    assert user.is_superadmin is True


def test_a_plain_role_reports_its_slugs_and_is_not_superadmin():
    user = _user(
        role=Role(
            name="Cashier Staff",
            permissions=["orders.read", "pos.register.access"],
            is_super_admin=False,
        )
    )
    assert user.is_superadmin is False
    dumped = UserResponse.model_validate(user)
    assert dumped.permissions == ["orders.read", "pos.register.access"]


def test_a_role_less_user_has_no_permissions_and_is_not_superadmin():
    user = _user()
    assert user.is_superadmin is False
    assert user.permissions == []
