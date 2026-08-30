"""The aggregator rider is mirrored into the shared fulfilment tables so the
order-details page shows one section for every order type."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.aggregators import aggregator_fulfilment as af

pytestmark = pytest.mark.asyncio


def _db(existing_delivery=None):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=existing_delivery)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


async def test_creates_delivery_row_and_records_rider(monkeypatch):
    recorded = []

    async def fake_record(db, delivery, driver):
        recorded.append((delivery, driver))

    monkeypatch.setattr(af.driver_assignment, "record", fake_record)
    db = _db(existing_delivery=None)
    order = SimpleNamespace(
        id=uuid.uuid4(), delivery_method="delivery", order_number="AGG-1"
    )

    await af.record_aggregator_fulfilment(
        db,
        order,
        channel="Keeta 2.0",
        driver_name="Rider Bob",
        driver_phone="+971500000001",
        driver_status="completed",
        delivery_fee=7.5,
    )

    assert db.add.call_count == 1
    delivery = db.add.call_args[0][0]
    assert delivery.provider == "keeta"  # "Keeta 2.0" → code
    assert delivery.courier_status == "completed"
    assert len(recorded) == 1 and recorded[0][1].name == "Rider Bob"


async def test_pickup_order_is_a_noop(monkeypatch):
    monkeypatch.setattr(af.driver_assignment, "record", AsyncMock())
    db = _db()
    order = SimpleNamespace(id=uuid.uuid4(), delivery_method="pickup")
    await af.record_aggregator_fulfilment(db, order, channel="Careem")
    db.scalar.assert_not_called()
    db.add.assert_not_called()


async def test_unknown_channel_falls_back_to_generic_and_no_rider(monkeypatch):
    monkeypatch.setattr(af.driver_assignment, "record", AsyncMock())
    db = _db(existing_delivery=None)
    order = SimpleNamespace(
        id=uuid.uuid4(), delivery_method="delivery", order_number="AGG-2"
    )
    # No driver on this one (e.g. Talabat/Keeta withhold it) → delivery row still
    # created, no rider stint.
    await af.record_aggregator_fulfilment(db, order, channel="Something Odd")
    delivery = db.add.call_args[0][0]
    assert delivery.provider == "aggregator"
    af.driver_assignment.record.assert_not_called()
