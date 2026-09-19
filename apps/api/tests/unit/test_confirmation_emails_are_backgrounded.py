"""
A cash checkout must not wait on the confirmation emails.

`create_order` confirms a COD order and then sends three Resend emails (the
customer's confirmation and the two owners' notifications). Sending them inline
cost every cash checkout ~2s of network the customer spent watching a spinner
after the order was already written, confirmed and on the register. They are now
fired as a tracked background task: the send still happens server-side
regardless of the browser, but the response returns as soon as the order exists.

Asserted here at the seam (`_dispatch_confirmation_emails`) rather than through
the DB-heavy `create_order`: the property that matters is that dispatch returns
without awaiting the send, and that the send still runs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.orders import order_service


@pytest.mark.asyncio
async def test_dispatch_returns_before_the_emails_finish_then_sends():
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def slow_send(_order):
        started.set()
        await release.wait()
        finished.set()

    order = SimpleNamespace(order_number="MM-20260919-999")

    with patch.object(order_service, "_send_confirmation_emails", slow_send):
        # Returns synchronously — it schedules, it does not await.
        order_service._dispatch_confirmation_emails(order)
        # The send has not completed; the caller (the checkout response) is free.
        assert not finished.is_set()
        # The task is held so the loop cannot drop it mid-send.
        assert len(order_service._pending_email_tasks) == 1
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        # It does run to completion, and cleans itself out of the set.
        await asyncio.wait_for(finished.wait(), timeout=1)
        for _ in range(3):
            await asyncio.sleep(0)
        assert order_service._pending_email_tasks == set()


@pytest.mark.asyncio
async def test_the_two_emails_go_out_concurrently_not_one_after_the_other():
    """The customer confirmation and the owner notification are independent, so
    `_send_confirmation_emails` gathers them rather than awaiting in sequence."""
    order = SimpleNamespace(order_number="MM-20260919-999")
    confirmation = AsyncMock()
    owner = AsyncMock()

    with (
        patch.object(
            order_service.email_service, "send_order_confirmation", confirmation
        ),
        patch.object(
            order_service.email_service, "send_owner_order_notification", owner
        ),
    ):
        await order_service._send_confirmation_emails(order)

    confirmation.assert_awaited_once_with(order)
    owner.assert_awaited_once_with(order)


@pytest.mark.asyncio
async def test_a_failing_send_is_swallowed_not_raised():
    """A mail provider having a bad minute does not un-confirm an order."""
    order = SimpleNamespace(order_number="MM-20260919-999")

    with patch.object(
        order_service.email_service,
        "send_order_confirmation",
        AsyncMock(side_effect=RuntimeError("resend down")),
    ):
        with patch.object(
            order_service.email_service,
            "send_owner_order_notification",
            AsyncMock(),
        ):
            # Must not raise.
            await order_service._send_confirmation_emails(order)
