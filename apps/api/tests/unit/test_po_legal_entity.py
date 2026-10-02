"""A purchase order's buying entity: stamped from its branch's counter config,
and it decides whether the PO's lines carry a recoverable VAT slice."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.inventory import inventory_service

FATEMA = SimpleNamespace(id=uuid.uuid4(), vat_registered=True)
NAJM = SimpleNamespace(id=uuid.uuid4(), vat_registered=False)


def _db(entity):
    db = SimpleNamespace(get=AsyncMock(return_value=entity))
    return db


@pytest.mark.asyncio
async def test_a_new_po_is_stamped_with_its_branchs_counter_entity():
    po = SimpleNamespace(branch_id=uuid.uuid4(), legal_entity_id=None)
    with patch.object(
        inventory_service.tax_identity_service,
        "resolve",
        AsyncMock(return_value=NAJM),
    ) as resolve:
        entity = await inventory_service.stamp_po_entity(_db(None), po)
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs == {"branch_id": po.branch_id, "source": "cashier"}
    assert entity is NAJM
    assert po.legal_entity_id == NAJM.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deductible", "entity", "expected"),
    [
        (True, FATEMA, True),
        # Barsha's counter entity is not VAT-registered: nothing to reclaim.
        (True, NAJM, False),
        (False, FATEMA, False),
        (False, NAJM, False),
    ],
)
async def test_vat_is_reclaimed_only_by_a_registered_entity_from_a_deductible_supplier(
    deductible, entity, expected
):
    po = SimpleNamespace(branch_id=uuid.uuid4(), legal_entity_id=entity.id)
    supplier = SimpleNamespace(is_vat_deductible=deductible)
    assert (
        await inventory_service.po_reclaims_vat(_db(entity), po, supplier) is expected
    )


@pytest.mark.asyncio
async def test_a_po_without_an_entity_resolves_it_from_the_branch():
    po = SimpleNamespace(branch_id=uuid.uuid4(), legal_entity_id=None)
    supplier = SimpleNamespace(is_vat_deductible=True)
    with patch.object(
        inventory_service.tax_identity_service,
        "resolve",
        AsyncMock(return_value=NAJM),
    ):
        assert await inventory_service.po_reclaims_vat(_db(None), po, supplier) is False
