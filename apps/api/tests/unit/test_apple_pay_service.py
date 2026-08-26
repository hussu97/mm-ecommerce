"""
Apple Pay is a Stripe surface, offered to everyone the device and the gateway
allow.

There is no account gate any more: the only server-side condition is that Stripe
is the active card gateway (Ziina does not offer Apple Pay), so an estate
switched to Ziina during an incident stops offering it. Eligibility is public —
a guest has no session to authenticate it with before their cart mints one — but
the intent endpoint, which spends money, stays owner-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.payments import apple_pay_service, payment_gateway_router


@dataclass
class _Choice:
    code: str


class TestEligibility:
    async def test_stripe_default_is_eligible(self, monkeypatch):
        async def _candidates(_db, _amount):
            return [_Choice("stripe"), _Choice("ziina")]

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(object(), amount=Decimal("50"))
        assert result == {"eligible": True}

    async def test_ziina_default_is_not_eligible(self, monkeypatch):
        async def _candidates(_db, _amount):
            return [_Choice("ziina"), _Choice("stripe")]

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(object(), amount=Decimal("50"))
        assert result == {"eligible": False}

    async def test_no_gateway_is_not_eligible(self, monkeypatch):
        async def _candidates(_db, _amount):
            return []

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(object(), amount=Decimal("50"))
        assert result == {"eligible": False}

    async def test_amount_defaults_to_the_stripe_floor(self, monkeypatch):
        # No amount named → the coarse probe still asks the gateway question at
        # a chargeable amount rather than zero.
        seen: list[Decimal] = []

        async def _candidates(_db, amount):
            seen.append(amount)
            return [_Choice("stripe")]

        monkeypatch.setattr(payment_gateway_router, "candidates", _candidates)
        result = await apple_pay_service.eligibility(object())
        assert result == {"eligible": True}
        assert seen == [Decimal("2.00")]


class TestEndpointAuth:
    async def test_eligibility_is_public(self, client):
        # No account gate: an unauthenticated caller gets an answer (here
        # `false`, since the mock DB has no active gateway rows) rather than a
        # 401 that would hide the option from every guest.
        response = await client.get("/api/v1/payments/apple-pay/eligibility")
        assert response.status_code == 200
        assert response.json() == {"eligible": False}

    async def test_intent_still_requires_auth(self, client):
        # The money-spending endpoint stays owner-only.
        response = await client.post(
            "/api/v1/payments/apple-pay/intent",
            json={"order_number": "MM-20260825-001"},
        )
        assert response.status_code == 401
