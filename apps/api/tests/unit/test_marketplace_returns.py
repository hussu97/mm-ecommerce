"""Marketplace returns — the Talabat return PIN and the box coming back.

Fixtures are real portal timelines read 2026-09-29 (order ids kept so the
reasoning can be re-checked against the portal):

- 3924587196 — picked up, rider back at the store, cancelled (customer
  unreachable): a return, PIN 7058.
- 3922595858 — cancelled for an unavailable item before any rider collected it:
  Talabat still mints a PIN (4807) and its portal badges it RETURNED, but
  nothing is coming back.
- 3919199257 — delivered, then cancelled over rider behaviour: the customer kept
  the food, so not a return either.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.marketplace_return import (
    MarketplaceReturn,
    MarketplaceReturnStatusEnum,
)
from app.models.order import OrderStatusEnum
from app.services.aggregators import marketplace_returns as mr
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.talabat_provider import (
    TalabatClient,
    is_return_candidate_row,
    parse_return_details,
)


def _statuses(*words, cancel=None):
    out = [
        {"status": w, "timestamp": f"2026-09-29T17:{i:02d}:00Z", "detail": {}}
        for i, w in enumerate(words)
    ]
    if cancel is not None:
        out.append(
            {
                "status": "CANCELLED",
                "timestamp": "2026-09-29T18:03:41Z",
                "detail": {"owner": cancel[0], "reason": cancel[1]},
            }
        )
    return out


RETURNED = {
    "pin": "7058",
    "returnOrderPin": None,
    "order": {"orderId": "3924587196", "status": "CANCELLED"},
    "orderStatuses": _statuses(
        "ACCEPTED",
        "COURIER_NEAR_PICK_UP",
        "ORDER_PREPARED",
        "PICKED_UP_BY_RIDER",
        "PICKED_UP",
        "RIDER_AT_VENDOR",
        cancel=("CUSTOMER", "UNABLE_TO_FIND"),
    ),
}
CANCELLED_BEFORE_PICKUP = {
    "pin": "4807",
    "returnOrderPin": None,
    "order": {"orderId": "3922595858", "status": "CANCELLED"},
    "orderStatuses": _statuses(
        "ACCEPTED", "COURIER_NEAR_PICK_UP", cancel=("VENDOR", "ITEM_UNAVAILABLE")
    ),
}
CANCELLED_AFTER_DELIVERY = {
    "pin": "2388",
    "returnOrderPin": None,
    "order": {"orderId": "3919199257", "status": "CANCELLED"},
    "orderStatuses": _statuses(
        "PICKED_UP", "DELIVERED", cancel=("TRANSPORT", "UNPROFESSIONAL_BEHAVIOUR")
    ),
}
IN_FLIGHT = {
    "pin": None,
    "returnOrderPin": None,
    "order": {"orderId": "3924673902", "status": "PICKED_UP"},
    "orderStatuses": _statuses("ACCEPTED", "PICKED_UP"),
}


# ── parse_return_details ──────────────────────────────────────────────────────


def test_a_picked_up_never_delivered_cancellation_is_a_return():
    d = parse_return_details(RETURNED)
    assert d["returning"] is True
    assert d["pin"] == "7058"
    assert d["cancel_owner"] == "CUSTOMER"
    assert d["cancel_reason"] == "UNABLE_TO_FIND"
    assert d["cancelled_at"] == "2026-09-29T18:03:41Z"


def test_a_pin_on_an_order_no_rider_collected_is_not_a_return():
    d = parse_return_details(CANCELLED_BEFORE_PICKUP)
    assert d["pin"] == "4807"  # minted all the same
    assert d["cancelled"] is True
    assert d["returning"] is False


def test_a_cancellation_after_delivery_is_not_a_return():
    d = parse_return_details(CANCELLED_AFTER_DELIVERY)
    assert d["returning"] is False


def test_an_order_still_in_flight_has_no_pin_and_is_not_cancelled():
    d = parse_return_details(IN_FLIGHT)
    assert d["pin"] is None
    assert d["cancelled"] is False
    assert d["returning"] is False


def test_return_order_pin_wins_when_talabat_ever_fills_it():
    d = parse_return_details({**RETURNED, "returnOrderPin": "1111"})
    assert d["pin"] == "1111"


# ── the CSV's own signal ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # 3924587196: out at 21:44, cancelled 22:03, never delivered.
        (
            {
                "Order status": "Cancelled",
                "In delivery at": "2026-09-29 21:44",
                "Delivered at": "",
            },
            True,
        ),
        # 3922595858: never went out.
        (
            {"Order status": "Cancelled", "In delivery at": "", "Delivered at": ""},
            False,
        ),
        # 3919199257: delivered, then cancelled.
        (
            {
                "Order status": "Cancelled",
                "In delivery at": "2026-09-27 14:53",
                "Delivered at": "2026-09-27 15:10",
            },
            False,
        ),
        (
            {
                "Order status": "Delivered",
                "In delivery at": "2026-09-27 14:53",
                "Delivered at": "",
            },
            False,
        ),
    ],
)
def test_is_return_candidate_row(row, expected):
    assert is_return_candidate_row(row) is expected


# ── folding a read into the row ───────────────────────────────────────────────


def _row(**kw) -> MarketplaceReturn:
    base = {
        "order_id": uuid.uuid4(),
        "channel": "talabat",
        "external_order_id": "3924587196",
        "status": MarketplaceReturnStatusEnum.PIN_PENDING.value,
        "return_pin": None,
    }
    base.update(kw)
    return MarketplaceReturn(**base)


def test_a_returning_read_moves_pending_to_awaiting_and_owes_one_push():
    row = _row()
    details = parse_return_details(RETURNED)
    assert mr._apply_details(row, details, source="trigger") is True
    assert row.status == "awaiting_return"
    assert row.return_pin == "7058"
    assert row.pin_source == "trigger"
    assert row.cancel_owner == "CUSTOMER"
    # The hourly scrape reading the same thing again owes nothing more.
    assert mr._apply_details(row, details, source="scrape") is False
    assert row.pin_source == "trigger"


def test_a_not_returning_read_dismisses_the_row_but_keeps_the_pin():
    row = _row()
    assert (
        mr._apply_details(
            row, parse_return_details(CANCELLED_BEFORE_PICKUP), source="trigger"
        )
        is False
    )
    assert row.status == "not_returning"
    assert row.return_pin == "4807"


def test_a_received_row_is_never_reopened_or_blanked():
    row = _row(status="received", return_pin="7058")
    mr._apply_details(
        row,
        {"pin": None, "returning": False, "cancelled": True},
        source="scrape",
    )
    assert row.status == "received"
    assert row.return_pin == "7058"


def test_a_csv_only_read_keeps_the_row_pending():
    row = _row()
    details = mr._details_from_csv(
        {
            "Order status": "Cancelled",
            "In delivery at": "2026-09-29 21:44",
            "Delivered at": "",
            "Cancellation owner": "Customer",
            "Cancellation reason": "Unable to find customer",
        }
    )
    assert mr._apply_details(row, details, source="scrape") is False
    assert row.status == "pin_pending"
    assert row.cancel_reason == "Unable to find customer"


# ── the GrubOps trigger ───────────────────────────────────────────────────────


def _order(**kw):
    base = {
        "id": uuid.uuid4(),
        "aggregator_channel": "talabat",
        "external_reference": "3924587196",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _db_with_row(row):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


@pytest.mark.parametrize(
    ("previous", "channel", "opens"),
    [
        (OrderStatusEnum.OUT_FOR_DELIVERY, "talabat", True),
        (OrderStatusEnum.PACKED, "talabat", True),
        (OrderStatusEnum.CONFIRMED, "talabat", False),
        (OrderStatusEnum.ARRIVED_AT_POS, "talabat", False),
        (OrderStatusEnum.OUT_FOR_DELIVERY, "noon_food", False),
        (OrderStatusEnum.OUT_FOR_DELIVERY, "deliveroo", False),
    ],
)
async def test_open_on_cancellation_gates_on_channel_and_previous(
    previous, channel, opens
):
    db = _db_with_row(None)
    with patch.object(mr, "_schedule_pin_read") as schedule:
        row = await mr.open_on_cancellation(
            db, _order(aggregator_channel=channel), previous=previous
        )
    assert (row is not None) is opens
    assert schedule.called is opens
    assert db.add.called is opens


async def test_open_on_cancellation_is_idempotent_and_skips_a_known_pin():
    existing = _row(status="awaiting_return", return_pin="7058")
    db = _db_with_row(existing)
    with patch.object(mr, "_schedule_pin_read") as schedule:
        row = await mr.open_on_cancellation(
            db, _order(), previous=OrderStatusEnum.OUT_FOR_DELIVERY
        )
    assert row is existing
    db.add.assert_not_called()
    schedule.assert_not_called()


async def test_the_kill_switch_stops_the_trigger():
    db = _db_with_row(None)
    with (
        patch.object(mr.settings, "TALABAT_RETURN_PIN_ENABLED", False),
        patch.object(mr, "_schedule_pin_read") as schedule,
    ):
        assert (
            await mr.open_on_cancellation(
                db, _order(), previous=OrderStatusEnum.OUT_FOR_DELIVERY
            )
            is None
        )
    schedule.assert_not_called()


# ── the scrape recorder ───────────────────────────────────────────────────────


def _agg(raw):
    return SimpleNamespace(
        channel="talabat",
        external_order_id="3924587196",
        raw=raw,
        cancelled_at=None,
    )


async def test_scrape_files_a_return_with_its_pin_and_pushes():
    db = _db_with_row(None)
    raw = {
        "Order status": "Cancelled",
        "In delivery at": "2026-09-29 21:44",
        "Delivered at": "",
        "_mm_return": parse_return_details(RETURNED),
    }
    with patch.object(mr, "_notify", new=AsyncMock()) as notify:
        row = await mr.record_from_scrape(db, _order(), _agg(raw))
    assert row.status == "awaiting_return"
    assert row.return_pin == "7058"
    assert row.pin_source == "scrape"
    notify.assert_awaited_once()


async def test_scrape_never_creates_a_row_the_portal_says_is_not_a_return():
    db = _db_with_row(None)
    raw = {
        "Order status": "Cancelled",
        "In delivery at": "",
        "_mm_return": parse_return_details(CANCELLED_BEFORE_PICKUP),
    }
    assert await mr.record_from_scrape(db, _order(), _agg(raw)) is None
    db.add.assert_not_called()


async def test_scrape_ignores_an_ordinary_cancellation():
    db = _db_with_row(None)
    raw = {"Order status": "Cancelled", "In delivery at": "", "Delivered at": ""}
    assert await mr.record_from_scrape(db, _order(), _agg(raw)) is None


# ── received back ─────────────────────────────────────────────────────────────


def _user():
    return SimpleNamespace(id=uuid.uuid4(), display_name="Aisha", email="a@x")


async def test_mark_received_stamps_and_restocks_once():
    row = _row(status="awaiting_return", return_pin="7058")
    db = _db_with_row(row)
    order = _order()
    restock_id = uuid.uuid4()
    with patch.object(mr, "_restock", new=AsyncMock(return_value=restock_id)) as rs:
        out = await mr.mark_received(db, order, user=_user())
        again = await mr.mark_received(db, order, user=_user())
    assert out is row and again is row
    assert row.status == "received"
    assert row.received_by_label == "Aisha"
    assert row.restock_transaction_id == restock_id
    rs.assert_awaited_once()


async def test_mark_received_refuses_an_order_not_expected_back():
    from app.core.exceptions import ConflictError

    for row in (None, _row(status="not_returning")):
        with pytest.raises(ConflictError):
            await mr.mark_received(_db_with_row(row), _order(), user=_user())


async def test_restock_skips_a_cancellation_already_disposed_of():
    db = MagicMock()
    # No return movement yet, but the console already resolved the exception
    # ("no inventory effect").
    db.scalar = AsyncMock(side_effect=[None, "posted"])
    with patch(
        "app.services.inventory.source_event_service.record_return", new=AsyncMock()
    ) as record:
        assert await mr._restock(db, _order(), _row(), user=_user()) is None
    record.assert_not_called()


async def test_restock_runs_the_full_restock_disposition():
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[None, "exception"])
    moved = SimpleNamespace(id=uuid.uuid4())
    order = _order()
    with patch(
        "app.services.inventory.source_event_service.record_return",
        new=AsyncMock(return_value=[moved]),
    ) as record:
        out = await mr._restock(db, order, _row(return_pin="7058"), user=_user())
    assert out == moved.id
    kwargs = record.await_args.kwargs
    assert kwargs["disposition"] == "restock"
    assert kwargs["idempotency_key"] == f"marketplace-return:{order.id}"
    assert "7058" in kwargs["notes"]


# ── the provider's scrape hook ────────────────────────────────────────────────


async def test_attach_return_details_reads_only_return_candidates():
    from app.services.aggregators.normalized import StandardOrder

    returning = StandardOrder(
        external_order_id="3924587196",
        external_outlet_id="711571",
        raw={
            "Order status": "Cancelled",
            "In delivery at": "2026-09-29 21:44",
            "Delivered at": "",
        },
    )
    ordinary = StandardOrder(
        external_order_id="3924554570",
        external_outlet_id="711571",
        raw={"Order status": "Delivered", "In delivery at": "x", "Delivered at": "y"},
    )
    client = TalabatClient()
    session = LoadedSession(channel="talabat", account_ref="")
    with patch.object(
        client,
        "fetch_return_details",
        new=AsyncMock(return_value=parse_return_details(RETURNED)),
    ) as fetch:
        await client._attach_return_details(session, [returning, ordinary])
    fetch.assert_awaited_once_with(session, order_id="3924587196", vendor_id="711571")
    assert returning.raw["_mm_return"]["pin"] == "7058"
    assert "_mm_return" not in ordinary.raw


async def test_fetch_return_details_sends_order_and_store():
    client = TalabatClient()
    session = LoadedSession(channel="talabat", account_ref="")
    with patch.object(
        client, "_graphql", new=AsyncMock(return_value={"orders": {"order": RETURNED}})
    ) as gql:
        out = await client.fetch_return_details(
            session, order_id="3924587196", vendor_id="711571"
        )
    assert out["pin"] == "7058"
    params = gql.await_args.kwargs["variables"]["params"]
    assert params == {
        "orderId": "3924587196",
        "GlobalVendorCode": {"globalEntityId": "TB_AE", "vendorId": "711571"},
    }


# ── cancel reason from the Talabat export ─────────────────────────────────────


def test_promote_reads_talabat_cancel_owner_and_reason():
    from app.services.aggregators.promote import _cancel_reason

    agg = SimpleNamespace(
        raw={
            "Cancellation owner": "Customer",
            "Cancellation reason": "Unable to find customer",
        }
    )
    assert _cancel_reason(agg) == "Customer: Unable to find customer"
    assert _cancel_reason(SimpleNamespace(raw={})) is None
