"""Two POS input guards: the percentage-charge bound and till ownership.

F-POS-21 — a percentage charge `value` is a FRACTION (0.10 = 10%). `value: 10`
would be 1000%, applied verbatim by `pos_pricing` as `base * value`, so the bound
lives at the boundary every writer of a charge shares.

F-POS-14 — `_resolve_till` accepted any `till_id` with no ownership check, so a
terminal could post a sale or a payment into another cashier's drawer. It now
mirrors `tills._assert_can_touch`: your own till, or an admin.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1.pos_orders import _resolve_till
from app.core.exceptions import ForbiddenError, NotFoundError
from app.schemas.pos.charges import ChargeCreate, ChargeUpdate
from app.schemas.pos_order import ApplyChargeRequest

_MODELS = [
    (ApplyChargeRequest, {}),
    (ChargeCreate, {"name": "Service"}),
    (ChargeUpdate, {}),
]


@pytest.mark.parametrize("model,extra", _MODELS)
def test_a_whole_number_percentage_charge_is_rejected(model, extra):
    # 10 as a percentage is 1000%. Every charge schema must refuse it.
    with pytest.raises(ValidationError):
        model(type="percentage", value=Decimal("10"), **extra)


@pytest.mark.parametrize("model,extra", _MODELS)
def test_a_fractional_percentage_and_any_fixed_amount_pass(model, extra):
    model(type="percentage", value=Decimal("0.10"), **extra)  # 10%, fine
    model(type="fixed", value=Decimal("10"), **extra)  # AED 10 flat, unbounded


@pytest.mark.asyncio
async def test_resolve_till_refuses_another_cashiers_drawer():
    owner_id = uuid.uuid4()
    till = SimpleNamespace(user_id=owner_id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=till)

    intruder = SimpleNamespace(id=uuid.uuid4(), is_admin=False)
    with pytest.raises(ForbiddenError):
        await _resolve_till(db, uuid.uuid4(), user=intruder)

    # The owner and any admin resolve it.
    owner = SimpleNamespace(id=owner_id, is_admin=False)
    assert await _resolve_till(db, uuid.uuid4(), user=owner) is till
    admin = SimpleNamespace(id=uuid.uuid4(), is_admin=True)
    assert await _resolve_till(db, uuid.uuid4(), user=admin) is till


@pytest.mark.asyncio
async def test_resolve_till_returns_none_or_404_before_ownership():
    user = SimpleNamespace(id=uuid.uuid4(), is_admin=False)
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    # No till referenced at all → None, no lookup.
    assert await _resolve_till(db, None, user=user) is None
    # A referenced-but-missing till is a 404, not a silent None.
    with pytest.raises(NotFoundError):
        await _resolve_till(db, uuid.uuid4(), user=user)
