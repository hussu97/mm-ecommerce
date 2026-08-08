"""
Which processor takes the card, and — more importantly — which one does not.

The reason this feature exists is a Stripe incident, so the tests that matter
most are the negative ones: production stays on Stripe, an unconfigured gateway
is unreachable no matter how many flags are set, and a refused card is never
quietly re-presented to a second processor.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models.payment_gateway import PaymentGateway
from app.services import payment_gateway_router as router
from app.services.providers.base import (
    GatewaySession,
    PaymentGatewayProvider,
)


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "089_payment_gateways.py"
)


def _migration():
    """
    The migration module, loaded by path.

    It cannot be imported normally — `089_payment_gateways` is not a legal
    identifier — and it is worth the ceremony: what production actually runs is
    this file, not a constant copied next to it.
    """
    spec = importlib.util.spec_from_file_location("_mig_089", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    code: str,
    *,
    is_active: bool = True,
    priority: int = 1,
    supports_failover: bool = True,
    min_amount: str | None = "2.00",
    max_amount: str | None = None,
    test_mode: bool = False,
) -> PaymentGateway:
    return PaymentGateway(
        code=code,
        name=code.title(),
        is_active=is_active,
        priority=priority,
        supports_failover=supports_failover,
        min_amount=Decimal(min_amount) if min_amount else None,
        max_amount=Decimal(max_amount) if max_amount else None,
        test_mode=test_mode,
    )


class _FakeProvider(PaymentGatewayProvider):
    """A gateway that does exactly what the test tells it to."""

    def __init__(self, code: str, *, configured: bool = True, raises=None):
        self.code = code
        self._configured = configured
        self._raises = raises
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    def create_session(self, order, *, test_mode: bool = False) -> GatewaySession:
        self.calls += 1
        if self._raises:
            raise self._raises
        return GatewaySession(
            session_id=f"{self.code}_sess_1",
            checkout_url=f"https://{self.code}.example/pay",
        )

    def parse_webhook(self, payload, headers):  # pragma: no cover — unused here
        raise NotImplementedError


def _db_returning(rows: list[PaymentGateway]) -> MagicMock:
    """A session whose every `select(PaymentGateway)` yields *rows*."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def providers(monkeypatch):
    """A registry the test owns, so a real key on the machine cannot leak in."""
    registry: dict[str, PaymentGatewayProvider] = {}
    monkeypatch.setattr(router, "PROVIDERS", registry)
    return registry


# ── the safety property: production stays on Stripe ───────────────────────────


class TestZiinaIsNotLiveInProduction:
    def test_ziina_is_unconfigured_by_default(self, monkeypatch):
        """
        The default posture, asserted rather than trusted.

        `ZIINA_ENABLED` is a *separate* switch from the API key on purpose: keys
        turn up on a VM for all sorts of reasons and none of them is a decision
        to route live card traffic through a new processor.
        """
        from app.services.providers.ziina_provider import provider as ziina

        monkeypatch.setattr(settings, "ZIINA_ENABLED", False)
        monkeypatch.setattr(settings, "ZIINA_API_KEY", "")
        assert not ziina.is_configured()

        # A key on its own is not enough.
        monkeypatch.setattr(settings, "ZIINA_API_KEY", "zk_live_real_key")
        assert not ziina.is_configured()

        # Neither is the flag on its own.
        monkeypatch.setattr(settings, "ZIINA_ENABLED", True)
        monkeypatch.setattr(settings, "ZIINA_API_KEY", "")
        assert not ziina.is_configured()

        # Both, deliberately, and only then.
        monkeypatch.setattr(settings, "ZIINA_API_KEY", "zk_live_real_key")
        assert ziina.is_configured()

    def test_the_migration_seeds_ziina_inactive(self):
        """
        The row ships switched off.

        This is the lock that means the release can go to production with no
        configuration change whatsoever and nothing about card checkout moves.
        Read out of the migration itself rather than out of a fixture, because
        the migration is what actually runs against the live database.
        """
        seeded = {code: is_active for code, _, is_active, *_ in _migration().GATEWAYS}

        assert seeded == {"stripe": True, "ziina": False}

    def test_the_migration_does_not_touch_the_gateway_column(self):
        """
        `orders.payment_provider` already holds `stripe` or `cod` per order,
        which is exactly the gateway under the new reading — so every existing
        row is already correct and a backfill could only make it worse. Only
        `payment_method` moves, and only `stripe → card`.
        """
        source = _MIGRATION_PATH.read_text()
        upgrade = source[source.index("def upgrade") : source.index("def downgrade")]

        assert "SET payment_method = 'card'" in upgrade
        assert "SET payment_provider" not in upgrade

    async def test_an_active_but_unconfigured_gateway_is_never_selected(
        self, providers
    ):
        """
        The lock the admin *cannot* undo. Credentials are environment, not
        database, so switching the row on in production selects nothing — which
        is exactly why the toggle is safe to ship there.
        """
        providers["stripe"] = _FakeProvider("stripe")
        providers["ziina"] = _FakeProvider("ziina", configured=False)
        db = _db_returning([_row("ziina", priority=1), _row("stripe", priority=2)])

        chosen = await router.select_gateway(db, Decimal("100.00"))

        assert chosen.code == "stripe"


# ── selection ─────────────────────────────────────────────────────────────────


class TestSelection:
    async def test_lowest_priority_number_wins(self, providers):
        providers["stripe"] = _FakeProvider("stripe")
        providers["ziina"] = _FakeProvider("ziina")
        db = _db_returning([_row("ziina", priority=1), _row("stripe", priority=2)])

        assert (await router.select_gateway(db, Decimal("50.00"))).code == "ziina"

    async def test_an_inactive_row_is_not_offered(self, providers):
        """`candidates` filters in SQL; this pins that the filter is actually on."""
        providers["stripe"] = _FakeProvider("stripe")
        db = _db_returning([_row("stripe")])

        options = await router.candidates(db, Decimal("50.00"))

        assert [o.code for o in options] == ["stripe"]
        # The WHERE clause is what excludes inactive rows, so assert it is there
        # rather than re-implementing the filter in the fake.
        assert "is_active" in str(db.execute.await_args.args[0])

    async def test_a_row_naming_nothing_implemented_is_skipped(self, providers):
        """A typo in `code` must not take card checkout down."""
        providers["stripe"] = _FakeProvider("stripe")
        db = _db_returning([_row("strpie", priority=1), _row("stripe", priority=2)])

        assert (await router.select_gateway(db, Decimal("50.00"))).code == "stripe"

    async def test_an_amount_below_every_floor_says_so(self, providers):
        """
        The customer gets a sentence they can act on, not an opaque 400 from
        someone else's API on the last screen of checkout.
        """
        providers["stripe"] = _FakeProvider("stripe")
        db = _db_returning([_row("stripe", min_amount="2.00")])

        with pytest.raises(BadRequestError, match="minimum chargeable amount"):
            await router.select_gateway(db, Decimal("1.50"))

    async def test_nothing_available_reads_as_an_outage_not_a_small_basket(
        self, providers
    ):
        providers["stripe"] = _FakeProvider("stripe", configured=False)
        db = _db_returning([_row("stripe")])

        with pytest.raises(BadRequestError, match="temporarily unavailable"):
            await router.select_gateway(db, Decimal("100.00"))

    async def test_a_ceiling_is_honoured(self, providers):
        providers["stripe"] = _FakeProvider("stripe")
        providers["ziina"] = _FakeProvider("ziina")
        db = _db_returning(
            [
                _row("stripe", priority=1, max_amount="100.00"),
                _row("ziina", priority=2),
            ]
        )

        assert (await router.select_gateway(db, Decimal("500.00"))).code == "ziina"


# ── failover ──────────────────────────────────────────────────────────────────


class TestFailover:
    def test_it_walks_past_what_has_already_failed(self):
        options = [
            router.GatewayChoice(_row("stripe", priority=1), _FakeProvider("stripe")),
            router.GatewayChoice(_row("ziina", priority=2), _FakeProvider("ziina")),
        ]

        assert router.failover_after(["stripe"], options).code == "ziina"
        assert router.failover_after(["stripe", "ziina"], options) is None

    def test_a_gateway_can_opt_out_of_being_a_reflex(self):
        """
        `supports_failover` is not `is_active`. "Use this one, but never
        automatically" is a real position — a processor with worse rates is a
        fine deliberate choice and a bad fallback — and conflating the two flags
        would make it unexpressable.
        """
        options = [
            router.GatewayChoice(_row("stripe", priority=1), _FakeProvider("stripe")),
            router.GatewayChoice(
                _row("ziina", priority=2, supports_failover=False),
                _FakeProvider("ziina"),
            ),
        ]

        assert router.failover_after(["stripe"], options) is None
