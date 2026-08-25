"""
Apple Pay is a Stripe surface for a named allowlist, and both halves of that
sentence are load-bearing.

The feature is offered to exactly the accounts in `APPLE_PAY_TEST_EMAILS` and
only while Stripe is the active card gateway — a guest, another customer, or an
estate switched to Ziina during an incident must all see nothing. These guard
the gate so a client coaxed into asking still gets a `false` and, at the
money-spending endpoint, a hard refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import ForbiddenError
from app.services.payments import apple_pay_service, payment_gateway_router


def _user(email: str, *, is_guest: bool = False, is_admin: bool = False):
    return SimpleNamespace(email=email, is_guest=is_guest, is_admin=is_admin, id="u-1")


class TestIsTestUser:
    def test_allowlisted_account_passes(self):
        assert apple_pay_service.is_test_user(_user("h_abbasi97@hotmail.com"))

    def test_match_is_case_and_space_insensitive(self):
        assert apple_pay_service.is_test_user(_user("  H_Abbasi97@Hotmail.com "))

    def test_other_account_is_refused(self):
        assert not apple_pay_service.is_test_user(_user("someone@else.com"))

    def test_guest_is_refused_even_if_email_matches(self):
        assert not apple_pay_service.is_test_user(
            _user("h_abbasi97@hotmail.com", is_guest=True)
        )

    def test_anonymous_is_refused(self):
        assert not apple_pay_service.is_test_user(None)


@dataclass
class _Choice:
    code: str


class TestEligibility:
    async def test_non_test_user_is_never_eligible(self, monkeypatch):
        # Not even a gateway lookup happens — the account gate is first.
        called = False

        async def _candidates(_db, _amount):
            nonlocal called
            called = True
            return [_Choice("stripe")]

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(
            object(), _user("someone@else.com"), amount=Decimal("50")
        )
        assert result == {"eligible": False}
        assert called is False

    async def test_test_user_with_stripe_default_is_eligible(self, monkeypatch):
        async def _candidates(_db, _amount):
            return [_Choice("stripe"), _Choice("ziina")]

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(
            object(), _user("h_abbasi97@hotmail.com"), amount=Decimal("50")
        )
        assert result == {"eligible": True}

    async def test_test_user_with_ziina_default_is_not_eligible(self, monkeypatch):
        async def _candidates(_db, _amount):
            return [_Choice("ziina"), _Choice("stripe")]

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(
            object(), _user("h_abbasi97@hotmail.com"), amount=Decimal("50")
        )
        assert result == {"eligible": False}

    async def test_no_gateway_is_not_eligible(self, monkeypatch):
        async def _candidates(_db, _amount):
            return []

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(
            object(), _user("h_abbasi97@hotmail.com"), amount=Decimal("50")
        )
        assert result == {"eligible": False}


class TestCreateIntentGuards:
    async def test_non_test_user_is_forbidden_before_any_work(self):
        # The allowlist is checked before the order is even loaded, so a
        # non-test caller cannot spend money or probe order numbers.
        with pytest.raises(ForbiddenError):
            await apple_pay_service.create_intent(
                object(), "MM-20260825-001", _user("someone@else.com")
            )


class TestEndpointsRequireAuth:
    async def test_eligibility_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/payments/apple-pay/eligibility")
        assert response.status_code == 401

    async def test_intent_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/payments/apple-pay/intent",
            json={"order_number": "MM-20260825-001"},
        )
        assert response.status_code == 401
