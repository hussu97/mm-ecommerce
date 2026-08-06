"""
A service charge is taxable, and the register was not taxing it.

`recalculate` built its `ChargeInput`s with `tax_cache.get(None, ...)`. The only
value that key can ever hold is the tuple `_resolve_tax(None)` returns —
`(0, "No tax", None, True)` — so every charge on every order was priced at 0%
VAT regardless of the tax group configured against it.

The pricing engine had never been wrong about this: `calculate_order` applies
charge tax correctly, and `test_pos_pricing.py` proves it. But those tests call
the pure function with an explicit rate, so they went on passing while the
caller fed it a zero, and production under-declared VAT on every delivery and
service charge it took. Hence this test goes through `recalculate` — the tax has
to be resolved, not merely resolvable.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.charge import Charge
from app.services import pos_order_service

pytestmark = pytest.mark.asyncio

VAT = Decimal("0.05")


def _charge_row(charge_id: uuid.UUID | None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        charge_id=charge_id,
        name="Delivery",
        type="fixed",
        value=Decimal("15.00"),
        amount=Decimal("0"),
        tax_amount=Decimal("0"),
        tax_exclusive_amount=Decimal("0"),
    )


@pytest.fixture
def order_with_one_charge():
    """An order with no lines and a single fixed charge — the charge is the subject."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        items=[],
        order_discounts=[],
        order_charges=[],
        order_taxes=[],
        subtotal=Decimal("0"),
        discount_amount=Decimal("0"),
        vat_amount=Decimal("0"),
        total_excl_vat=Decimal("0"),
        total=Decimal("0"),
        rounding_amount=Decimal("0"),
    )


@pytest.fixture
def captured_charges(monkeypatch):
    """Intercept what recalculate hands the pricing engine."""
    seen: list = []
    real = pos_order_service.pos_pricing.calculate_order

    def spy(lines, *, charges=None, **kwargs):
        seen.extend(charges or [])
        return real(lines, charges=charges, **kwargs)

    monkeypatch.setattr(pos_order_service.pos_pricing, "calculate_order", spy)
    return seen


async def _run(monkeypatch, order, *, configured_tax_group, resolved_rate):
    db = AsyncMock()
    db.add = MagicMock()

    async def fake_get(model, pk):
        if model is Charge:
            return SimpleNamespace(id=pk, tax_group_id=configured_tax_group)
        return None

    db.get = AsyncMock(side_effect=fake_get)

    monkeypatch.setattr(pos_order_service, "get_order", AsyncMock(return_value=order))
    monkeypatch.setattr(
        pos_order_service,
        "_settings",
        AsyncMock(return_value=SimpleNamespace(cash_rounding_step=0)),
    )
    monkeypatch.setattr(
        pos_order_service,
        "_resolve_tax",
        AsyncMock(
            return_value=(resolved_rate, "VAT 5%", str(uuid.uuid4()), True)
            if resolved_rate
            else (Decimal("0"), "No tax", None, True)
        ),
    )
    return await pos_order_service.recalculate(db, order)


class TestChargeTax:
    async def test_charge_is_rated_by_its_own_tax_group(
        self, monkeypatch, order_with_one_charge, captured_charges
    ):
        tax_group = uuid.uuid4()
        order_with_one_charge.order_charges = [_charge_row(uuid.uuid4())]

        await _run(
            monkeypatch,
            order_with_one_charge,
            configured_tax_group=tax_group,
            resolved_rate=VAT,
        )

        assert captured_charges, "recalculate priced no charges"
        assert captured_charges[0].tax_rate == VAT, (
            "the charge reached the pricing engine zero-rated — the tax group "
            "configured against it was never looked up"
        )

    async def test_charge_tax_lands_on_the_order(
        self, monkeypatch, order_with_one_charge
    ):
        order_with_one_charge.order_charges = [_charge_row(uuid.uuid4())]

        await _run(
            monkeypatch,
            order_with_one_charge,
            configured_tax_group=uuid.uuid4(),
            resolved_rate=VAT,
        )

        assert order_with_one_charge.vat_amount > 0, (
            "an AED 15 tax-inclusive delivery charge declared no VAT"
        )

    async def test_ad_hoc_charge_without_config_stays_untaxed(
        self, monkeypatch, order_with_one_charge, captured_charges
    ):
        """
        An open charge typed at the counter has no `Charge` row and therefore no
        configured group. Zero is the honest answer — inventing a rate would be
        guessing at the till.
        """
        order_with_one_charge.order_charges = [_charge_row(None)]

        await _run(
            monkeypatch,
            order_with_one_charge,
            configured_tax_group=None,
            resolved_rate=None,
        )

        assert captured_charges[0].tax_rate == Decimal("0")
