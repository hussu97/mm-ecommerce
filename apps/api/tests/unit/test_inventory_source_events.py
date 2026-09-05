from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError
from app.models.inventory_v2 import (
    InventorySourceEvent,
    InventorySourceEventStatusEnum,
)
from app.services.inventory import source_event_service


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False


def _db():
    return SimpleNamespace(
        begin_nested=lambda: _Savepoint(),
        refresh=AsyncMock(),
        flush=AsyncMock(),
    )


def _event() -> InventorySourceEvent:
    return InventorySourceEvent(
        id=uuid4(),
        branch_id=uuid4(),
        source_type="order",
        source_id=str(uuid4()),
        idempotency_key=f"order:{uuid4()}:1",
        status=InventorySourceEventStatusEnum.PENDING.value,
        accepted_at=None,
    )


@pytest.mark.asyncio
async def test_domain_posting_failure_becomes_a_no_movement_exception(monkeypatch):
    db = _db()
    event = _event()
    monkeypatch.setattr(
        source_event_service,
        "post_event",
        AsyncMock(side_effect=ConflictError("Insufficient stock")),
    )

    result = await source_event_service._post_or_record_exception(
        db,
        event=event,
        order=SimpleNamespace(),
        user=None,
        already_locked=True,
    )

    assert result is None
    assert event.status == InventorySourceEventStatusEnum.EXCEPTION.value
    assert event.error_code == "inventory_posting_failed"
    assert event.error_detail == "Insufficient stock"
    db.refresh.assert_awaited_once_with(event)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_posting_failure_still_rolls_back_the_order(monkeypatch):
    db = _db()
    event = _event()
    monkeypatch.setattr(
        source_event_service,
        "post_event",
        AsyncMock(side_effect=RuntimeError("database connection lost")),
    )

    with pytest.raises(RuntimeError, match="database connection lost"):
        await source_event_service._post_or_record_exception(
            db,
            event=event,
            order=SimpleNamespace(),
            user=None,
            already_locked=True,
        )

    db.refresh.assert_not_awaited()
    db.flush.assert_not_awaited()
