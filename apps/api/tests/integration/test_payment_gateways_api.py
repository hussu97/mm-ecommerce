"""
The admin switch, and the two things it refuses to do.

Both refusals are enforced on the server rather than in the screen, because both
failure modes are silent. Activating a gateway with no credentials produces a row
that looks on and routes nothing — the operator's mental model and the system's
behaviour disagree, and neither says so. Switching off the last working gateway
is not a configuration, it is an outage, and it should take more than one click.

The first refusal is also the lock that lets this page ship to production while
Ziina is not signed off: credentials are environment, not database, so the
toggle is there and cannot do anything.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.deps import get_current_active_user
from app.models.payment_gateway import PaymentGateway
from app.models.user import User


def _gateway(code, *, is_active=True, priority=1, min_amount="2.00"):
    return PaymentGateway(
        code=code,
        name=code.title(),
        is_active=is_active,
        priority=priority,
        supports_failover=True,
        min_amount=Decimal(min_amount),
        max_amount=None,
        test_mode=False,
    )


class _Provider:
    def __init__(self, configured: bool):
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured


@pytest.fixture
def admin_client(client):
    """The gateway screen is admin-only; the auth itself is tested elsewhere."""
    from app.main import app

    app.dependency_overrides[get_current_active_user] = lambda: User(
        email="admin@example.com", is_admin=True, is_active=True
    )
    yield client
    app.dependency_overrides.pop(get_current_active_user, None)


@pytest.fixture
def registry(monkeypatch):
    """A provider registry the test owns, so a stray key cannot leak in."""
    import app.api.v1.payment_gateways as module
    import app.services.payments.payment_gateway_router as router

    providers: dict = {}
    monkeypatch.setattr(module, "PROVIDERS", providers)
    monkeypatch.setattr(router, "PROVIDERS", providers)
    return providers


def _rows(mock_db, rows, *, one=None):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar_one_or_none = MagicMock(return_value=one)
    mock_db.execute = AsyncMock(return_value=result)


class TestListing:
    async def test_it_reports_whether_the_environment_can_use_each_gateway(
        self, admin_client, mock_db, registry
    ):
        """
        `is_configured` is computed, never stored — it is a fact about the
        running container, and a column would be a copy of it that goes stale
        the moment a secret rotates.
        """
        registry["stripe"] = _Provider(True)
        registry["ziina"] = _Provider(False)
        _rows(mock_db, [_gateway("stripe"), _gateway("ziina", is_active=False)])

        response = await admin_client.get("/api/v1/payment-gateways")

        assert response.status_code == 200
        by_code = {g["code"]: g for g in response.json()}
        assert by_code["stripe"] == {
            **by_code["stripe"],
            "is_configured": True,
            "is_routable": True,
        }
        # The production posture: present, off, and unusable even if switched on.
        assert by_code["ziina"]["is_configured"] is False
        assert by_code["ziina"]["is_routable"] is False


class TestTheLocks:
    async def test_an_unconfigured_gateway_cannot_be_activated(
        self, admin_client, mock_db, registry
    ):
        """
        The lock the admin cannot undo, and the reason this page is safe on
        production. Someone clicking "switch on Ziina" there gets a sentence
        explaining why nothing happened, not a row that lies.
        """
        registry["ziina"] = _Provider(False)
        _rows(mock_db, [], one=_gateway("ziina", is_active=False))

        response = await admin_client.patch(
            "/api/v1/payment-gateways/ziina", json={"is_active": True}
        )

        assert response.status_code == 400
        assert "no credentials" in response.json()["detail"]

    async def test_the_last_working_gateway_cannot_be_switched_off(
        self, admin_client, mock_db, registry
    ):
        registry["stripe"] = _Provider(True)
        registry["ziina"] = _Provider(False)
        stripe = _gateway("stripe")
        # `_assert_not_the_last_one` asks for the *other* active rows.
        _rows(mock_db, [_gateway("ziina")], one=stripe)

        response = await admin_client.patch(
            "/api/v1/payment-gateways/stripe", json={"is_active": False}
        )

        # Ziina is active in this fixture and still does not count: an active
        # gateway with no keys is not a fallback, it is the same outage with an
        # extra step.
        assert response.status_code == 400
        assert "only working card gateway" in response.json()["detail"]
        assert stripe.is_active is True

    async def test_it_can_be_switched_off_once_something_else_works(
        self, admin_client, mock_db, registry
    ):
        registry["stripe"] = _Provider(True)
        registry["ziina"] = _Provider(True)
        stripe = _gateway("stripe")
        _rows(mock_db, [_gateway("ziina")], one=stripe)

        response = await admin_client.patch(
            "/api/v1/payment-gateways/stripe", json={"is_active": False}
        )

        assert response.status_code == 200
        assert stripe.is_active is False

    async def test_a_range_that_takes_no_orders_is_refused(
        self, admin_client, mock_db, registry
    ):
        registry["stripe"] = _Provider(True)
        _rows(mock_db, [], one=_gateway("stripe"))

        response = await admin_client.patch(
            "/api/v1/payment-gateways/stripe",
            json={"min_amount": "500.00", "max_amount": "100.00"},
        )

        assert response.status_code == 400
        assert "takes no orders" in response.json()["detail"]

    async def test_an_unknown_gateway_is_a_404(self, admin_client, mock_db, registry):
        _rows(mock_db, [], one=None)

        response = await admin_client.patch(
            "/api/v1/payment-gateways/paypal", json={"is_active": True}
        )

        assert response.status_code == 404


class TestPriority:
    async def test_reordering_needs_no_activation_change(
        self, admin_client, mock_db, registry
    ):
        """
        Making Ziina primary is a priority edit, not a pair of toggles. The
        two-step version has a window in the middle where both are off or both
        are fighting, and that window is a checkout outage.
        """
        registry["ziina"] = _Provider(True)
        ziina = _gateway("ziina", priority=2)
        _rows(mock_db, [], one=ziina)

        response = await admin_client.patch(
            "/api/v1/payment-gateways/ziina", json={"priority": 1}
        )

        assert response.status_code == 200
        assert ziina.priority == 1
