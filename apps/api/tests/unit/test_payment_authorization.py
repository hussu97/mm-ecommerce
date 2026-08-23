"""
An order number is not a credential.

`MM-YYYYMMDD-NNN` is the date plus a counter, so a valid one for someone else's
order is a guess away. Until these checks existed, that guess was enough to POST
`{"provider": "cod"}` at a stranger's pickup order and move it to CONFIRMED —
the shop bakes the cake, the customer gets a confirmation email, and no money
was ever taken. The same guess reset a `payment_failed` order back to `created`
and read back its payment provider and id.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import ForbiddenError
from app.services.payments.payment_service import _assert_may_act_on


class _Order:
    def __init__(self, user_id):
        self.user_id = user_id


class TestPaymentOwnership:
    def test_owner_may_act(self):
        owner = uuid.uuid4()
        _assert_may_act_on(_Order(owner), owner)

    def test_stranger_is_refused(self):
        with pytest.raises(ForbiddenError):
            _assert_may_act_on(_Order(uuid.uuid4()), uuid.uuid4())

    def test_anonymous_caller_is_refused(self):
        with pytest.raises(ForbiddenError):
            _assert_may_act_on(_Order(uuid.uuid4()), None)

    def test_orphaned_order_is_not_a_free_for_all(self):
        """
        `orders.user_id` is ON DELETE SET NULL, so a purged guest leaves orders
        with no owner. A null on both sides must not read as a match.
        """
        with pytest.raises(ForbiddenError):
            _assert_may_act_on(_Order(None), None)

    def test_admin_bypasses_ownership(self):
        _assert_may_act_on(_Order(uuid.uuid4()), uuid.uuid4(), admin=True)


class TestPaymentEndpointsRequireAuth:
    async def test_create_session_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/payments/create-session",
            json={"order_number": "MM-20260806-001", "provider": "cod"},
        )
        assert response.status_code == 401

    async def test_payment_status_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/payments/MM-20260806-001/status")
        assert response.status_code == 401
