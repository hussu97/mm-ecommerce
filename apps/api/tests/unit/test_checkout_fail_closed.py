"""
A checkout must never show success for an order that was not saved.

Services flush and the request-scoped `get_db` commits after the handler
returns — but with `BaseHTTPMiddleware` that commit runs after the 201 is
already handed back, so under DB-pool exhaustion a commit landing on a reaped
connection lost the row while the browser saw "Order placed" (MM-20260919-007).
The create endpoint now commits inside the request and turns a commit failure
into a 503 the customer can retry.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import orders as orders_route
from app.core.exceptions import ServiceUnavailableError


def _data():
    # No client_request_id, so the endpoint's idempotency pre-check is skipped.
    return SimpleNamespace(client_request_id=None, email="c@example.com")


@pytest.mark.asyncio
async def test_a_commit_that_fails_becomes_a_503_not_a_confirmed_order(monkeypatch):
    order = SimpleNamespace(order_number="MM-20260919-007")
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("connection is closed")),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        orders_route.order_service,
        "create_order",
        AsyncMock(return_value=order),
    )

    with pytest.raises(ServiceUnavailableError):
        await orders_route.create_order(
            _data(),
            SimpleNamespace(status_code=201),
            db=db,
            current_user=None,
        )

    # The half-written transaction is rolled back rather than left dangling.
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_successful_commit_returns_the_order(monkeypatch):
    order = SimpleNamespace(order_number="MM-20260919-008")
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(
        orders_route.order_service,
        "create_order",
        AsyncMock(return_value=order),
    )

    result = await orders_route.create_order(
        _data(),
        SimpleNamespace(status_code=201),
        db=db,
        current_user=None,
    )

    assert result is order
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
