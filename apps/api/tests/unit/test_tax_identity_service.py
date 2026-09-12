"""The (branch, channel) → legal-entity resolver and the VAT-stripping helper.

Covers what the write paths depend on: the channel→class map, the fallback to
the registered default entity, a channel pointing at a non-registered entity,
and that stripping VAT off a priced order leaves the customer's total untouched
while the reconciliation invariant holds.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.orders import order_pricing, tax_identity_service


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    """Answers `resolve`'s queries. `execute_results` are returned in order by
    `execute(...).scalar_one_or_none()` (the config lookup, then the default-entity
    lookup); `get_result` is what `db.get(LegalEntity, id)` returns."""

    def __init__(self, *, execute_results=None, get_result=None):
        self._queue = list(execute_results or [])
        self._get = get_result

    async def execute(self, _stmt):
        return _FakeResult(self._queue.pop(0) if self._queue else None)

    async def get(self, _model, _id):
        return self._get


_FATEMA = SimpleNamespace(
    id=uuid.uuid4(), reference="fatema", vat_registered=True, brand_name="MM Cakes"
)
_NAJM = SimpleNamespace(
    id=uuid.uuid4(), reference="najm", vat_registered=False, brand_name="Attibassi"
)


def test_channel_class_maps_the_three_sources_and_defaults_to_website():
    assert tax_identity_service.channel_class_for("cashier") == "counter"
    assert tax_identity_service.channel_class_for("online") == "website"
    assert tax_identity_service.channel_class_for("aggregator") == "aggregator"
    assert tax_identity_service.channel_class_for(None) == "website"
    assert tax_identity_service.channel_class_for("api") == "website"


def test_is_vat_registered_defaults_true_for_a_missing_entity():
    assert tax_identity_service.is_vat_registered(None) is True
    assert tax_identity_service.is_vat_registered(_FATEMA) is True
    assert tax_identity_service.is_vat_registered(_NAJM) is False


def test_stamp_sets_the_legal_entity_id():
    order = SimpleNamespace(legal_entity_id=None)
    tax_identity_service.stamp(order, _NAJM)
    assert order.legal_entity_id == _NAJM.id
    # A missing entity leaves it untouched (never silently nulled).
    order2 = SimpleNamespace(legal_entity_id="keep")
    tax_identity_service.stamp(order2, None)
    assert order2.legal_entity_id == "keep"


@pytest.mark.asyncio
async def test_no_branch_falls_back_to_the_registered_default():
    # No branch → the default-entity query, which returns Fatema.
    db = _FakeDB(execute_results=[_FATEMA])
    entity = await tax_identity_service.resolve(db, branch_id=None, source="cashier")
    assert entity is _FATEMA


@pytest.mark.asyncio
async def test_no_config_row_falls_back_to_the_registered_default():
    # First execute() → None (no config), then the default-entity query → Fatema.
    db = _FakeDB(execute_results=[None, _FATEMA])
    entity = await tax_identity_service.resolve(
        db, branch_id=uuid.uuid4(), source="online"
    )
    assert entity is _FATEMA


@pytest.mark.asyncio
async def test_a_config_row_resolves_its_entity():
    config = SimpleNamespace(legal_entity_id=_NAJM.id)
    db = _FakeDB(execute_results=[config], get_result=_NAJM)
    entity = await tax_identity_service.resolve(
        db, branch_id=uuid.uuid4(), source="cashier"
    )
    assert entity is _NAJM
    assert tax_identity_service.is_vat_registered(entity) is False


def test_apply_non_registered_zeroes_vat_and_keeps_the_total():
    totals = order_pricing.OrderTotals(
        subtotal=Decimal("110.00"),
        discount_amount=Decimal("10.00"),
        delivery_fee=Decimal("0.00"),
        low_order_fee=Decimal("0.00"),
        vat_rate=Decimal("0.0500"),
        vat_amount=Decimal("4.76"),
        total_excl_vat=Decimal("95.24"),
        total=Decimal("100.00"),
        taxes=[SimpleNamespace(name="VAT")],
        zone=None,
        delivery=None,
        delivery_fee_known=True,
        serviceable=True,
    )
    out = order_pricing.apply_non_registered(totals)
    assert out.vat_amount == Decimal("0")
    assert out.vat_rate == Decimal("0")
    assert out.taxes == []
    assert out.total == Decimal("100.00")
    assert out.total_excl_vat == Decimal("100.00")
    assert out.total_excl_vat + out.vat_amount == out.subtotal - out.discount_amount
