"""POST /inventory/transactions accepts only safe manual documents (F-INV-11).

The endpoint used to take any transaction type, with no idempotency key and a
cross-branch field: a bare `transfer_send` posted here decremented stock with no
paired receive and evaporated it. The request schema now restricts the type,
requires an idempotency key, and forbids the transfer fields.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.inventory import InventoryTransactionCreate


def _base(**overrides):
    data = {
        "type": "quantity_adjustment",
        "branch_id": str(uuid4()),
        "idempotency_key": "manual-adjust-0001",
        "items": [{"item_id": str(uuid4()), "quantity": "5"}],
    }
    data.update(overrides)
    return data


def test_a_manual_adjustment_is_accepted():
    model = InventoryTransactionCreate(**_base())
    assert model.type == "quantity_adjustment"
    assert model.idempotency_key == "manual-adjust-0001"


@pytest.mark.parametrize(
    "blocked_type",
    [
        "transfer_send",
        "transfer_receive",
        "consumption_from_orders",
        "return_from_orders",
        "waste_from_orders",
        "consumption_from_production",
        "production",
    ],
)
def test_system_coupled_types_are_refused(blocked_type):
    with pytest.raises(ValidationError):
        InventoryTransactionCreate(**_base(type=blocked_type))


def test_idempotency_key_is_required():
    payload = _base()
    del payload["idempotency_key"]
    with pytest.raises(ValidationError):
        InventoryTransactionCreate(**payload)


def test_idempotency_key_has_a_minimum_length():
    with pytest.raises(ValidationError):
        InventoryTransactionCreate(**_base(idempotency_key="short"))


def test_other_branch_id_is_refused():
    with pytest.raises(ValidationError):
        InventoryTransactionCreate(**_base(other_branch_id=str(uuid4())))


def test_other_warehouse_id_is_refused():
    with pytest.raises(ValidationError):
        InventoryTransactionCreate(**_base(other_warehouse_id=str(uuid4())))
