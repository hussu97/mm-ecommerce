"""The (branch, channel) VAT + trade-license resolver and the VAT-stripping helper.

Covers what the write paths depend on: a branch with no config behaves exactly
as today (registered, inherited), a non-registered channel books no VAT and
never inherits the branch's TRN, and stripping VAT off a priced order leaves the
customer's total untouched while the reconciliation invariant still holds.
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

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)


class _FakeDB:
    """A session that answers the two queries `resolve` makes, in order.

    `resolve` runs `execute(config query)` first and, only when the resolved
    invoice title is null, `execute(business settings)` — so the queue holds the
    config result then the settings result. `get(Branch, id)` returns `branch`.
    """

    def __init__(self, *, config, branch, settings):
        self._results = [_FakeResult(config), _FakeResult(settings)]
        self._branch = branch

    async def execute(self, _stmt):
        return self._results.pop(0)

    async def get(self, _model, _id):
        return self._branch


def _config(**over):
    base = dict(
        vat_registered=True,
        tax_group_id=None,
        tax_number=None,
        tax_registration_name=None,
        invoice_title=None,
        is_active=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


_BRANCH = SimpleNamespace(
    tax_number="100123456700003",
    tax_registration_name="Melting Moments Cakes LLC",
)
_SETTINGS = SimpleNamespace(invoice_title="Tax Invoice")


def test_channel_class_maps_the_three_sources_and_defaults_to_website():
    assert tax_identity_service.channel_class_for("cashier") == "counter"
    assert tax_identity_service.channel_class_for("online") == "website"
    assert tax_identity_service.channel_class_for("aggregator") == "aggregator"
    # Anything unexpected resolves to website (VAT-registered), never to a
    # silently non-registered class.
    assert tax_identity_service.channel_class_for(None) == "website"
    assert tax_identity_service.channel_class_for("api") == "website"


@pytest.mark.asyncio
async def test_no_branch_is_the_registered_inherited_default():
    identity = await tax_identity_service.resolve(
        _FakeDB(config=None, branch=None, settings=None),
        branch_id=None,
        source="cashier",
    )
    assert identity.vat_registered is True
    assert identity.tax_number is None
    assert identity.tax_registration_name is None
    assert identity.invoice_title is None


@pytest.mark.asyncio
async def test_no_config_row_is_the_registered_inherited_default():
    db = _FakeDB(config=None, branch=_BRANCH, settings=_SETTINGS)
    identity = await tax_identity_service.resolve(
        db, branch_id=uuid.uuid4(), source="online"
    )
    assert identity == tax_identity_service.TaxIdentity(
        vat_registered=True,
        tax_group_id=None,
        tax_number=None,
        tax_registration_name=None,
        invoice_title=None,
    )


@pytest.mark.asyncio
async def test_registered_config_inherits_branch_identity_per_field():
    # A seeded default row: registered, all identity fields null → inherits the
    # branch TRN/name and the business invoice title.
    db = _FakeDB(config=_config(), branch=_BRANCH, settings=_SETTINGS)
    identity = await tax_identity_service.resolve(
        db, branch_id=uuid.uuid4(), source="online"
    )
    assert identity.vat_registered is True
    assert identity.tax_number == "100123456700003"
    assert identity.tax_registration_name == "Melting Moments Cakes LLC"
    assert identity.invoice_title == "Tax Invoice"


@pytest.mark.asyncio
async def test_non_registered_config_books_no_vat_and_never_inherits_the_branch_trn():
    # Barsha's counter: not registered, its own entity name, no TRN configured.
    db = _FakeDB(
        config=_config(
            vat_registered=False,
            tax_registration_name="Barsha Sweets Trading LLC",
        ),
        branch=_BRANCH,
        settings=_SETTINGS,
    )
    identity = await tax_identity_service.resolve(
        db, branch_id=uuid.uuid4(), source="cashier"
    )
    assert identity.vat_registered is False
    # Never the branch's registered TRN.
    assert identity.tax_number is None
    assert identity.tax_registration_name == "Barsha Sweets Trading LLC"
    # A non-registered business may not issue a "Tax Invoice".
    assert identity.invoice_title == "Invoice"


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
    # The customer pays the same; VAT simply moves into the net.
    assert out.total == Decimal("100.00")
    assert out.total_excl_vat == Decimal("100.00")
    # The invoice-reconciliation invariant still holds.
    assert out.total_excl_vat + out.vat_amount == out.subtotal - out.discount_amount
