"""A day-series number lost to a concurrent writer is drawn again, not fatal."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.orders import order_numbers


def _clash(index: str) -> IntegrityError:
    return IntegrityError(
        "INSERT",
        {},
        Exception(f'duplicate key value violates unique constraint "{index}"'),
    )


def _db(*flush_effects):
    return SimpleNamespace(
        add=lambda _o: None,
        flush=AsyncMock(side_effect=list(flush_effects)),
        begin_nested=nullcontext,
    )


@pytest.mark.asyncio
async def test_a_number_taken_by_another_writer_is_drawn_again():
    # 2026-10-12: promotion and the GrubOps ingest both drew AGG-20261012-046.
    order = SimpleNamespace(order_number="AGG-20261012-046")
    db = _db(_clash("ix_orders_order_number"), None)
    next_number = AsyncMock(return_value="AGG-20261012-047")

    await order_numbers.add_with_fresh_number(db, order, next_number)

    assert order.order_number == "AGG-20261012-047"
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_any_other_constraint_is_a_real_error():
    order = SimpleNamespace(order_number="AGG-20261012-046")
    db = _db(_clash("uq_orders_source_external_reference"))
    next_number = AsyncMock()

    with pytest.raises(IntegrityError):
        await order_numbers.add_with_fresh_number(db, order, next_number)
    next_number.assert_not_awaited()
