"""
Who is allowed to do what on the register.

Three separate holes, all of the same kind — a check that was declared and then
not made:

* `add_item` gated only the open-price case, so any authenticated active user
  could add priced lines to any check whose id they could name.
* `get_current_active_user` checked `is_active` and nothing else. A storefront
  customer's JWT is the same format from the same `create_access_token`, so it
  authenticated against the register API and was stopped only by `user.can(...)`
  returning False for a role-less account.
* Refunds were gated on `pos.payment.perform`, so anyone who could take money
  could hand it back.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.deps import get_current_active_user
from app.models.role import ALL_PERMISSIONS


def _user(*, is_staff=False, is_admin=False, is_active=True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_active=is_active,
        is_staff=is_staff,
        is_admin=is_admin,
    )


def _request(*, is_pos_app: bool) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(is_pos_app=is_pos_app))
    )


@pytest.mark.asyncio
class TestRegisterApiIsStaffOnly:
    async def test_customer_token_is_refused_by_the_register_api(self):
        with pytest.raises(HTTPException) as excinfo:
            await get_current_active_user(_request(is_pos_app=True), _user())
        assert excinfo.value.status_code == 403
        assert "Staff" in excinfo.value.detail

    async def test_staff_token_is_accepted(self):
        user = _user(is_staff=True)
        assert await get_current_active_user(_request(is_pos_app=True), user) is user

    async def test_admin_token_is_accepted(self):
        user = _user(is_admin=True)
        assert await get_current_active_user(_request(is_pos_app=True), user) is user

    async def test_customer_token_still_works_on_the_storefront_api(self):
        """The gate is the register API, not the account — customers still shop."""
        user = _user()
        assert await get_current_active_user(_request(is_pos_app=False), user) is user

    async def test_inactive_account_is_still_refused_everywhere(self):
        with pytest.raises(HTTPException) as excinfo:
            await get_current_active_user(
                _request(is_pos_app=False), _user(is_active=False)
            )
        assert excinfo.value.status_code == 403


class TestRefundPermissionExists:
    def test_refund_is_its_own_permission(self):
        """
        Declared separately from `pos.payment.perform` so a cashier role can take
        money without being able to give it back.
        """
        assert "pos.payment.refund" in ALL_PERMISSIONS
        assert "pos.payment.perform" in ALL_PERMISSIONS
