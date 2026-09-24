"""
What an email log row records must fit the column and land in the right one.

On 2026-09-24 three inventory-report email log rows were lost: the sender logged
the report's UUID (36 chars) as the "order number" into a VARCHAR(30). Migration
284 had also widened `orders.order_number` to 40 for local-first counter sales,
whose pricing-mismatch alert logs that number too. And the admin links
`order_number` to `/orders/{n}`, so a report id or a transfer reference there was
a broken order link even when it fitted. Migration 286 widened the column and
gave non-order emails a `reference` of their own.

DB-free: `_send` is stubbed and `_log`'s session factory is replaced, so the
row that would be written is captured as built.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.email_log import EmailLog
from app.services import email_service

#: The longest order number an order can carry since migration 284.
_ORDERS_ORDER_NUMBER_LENGTH = 40


class _CapturingSession:
    def __init__(self, rows: list[EmailLog]):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, row):
        self._rows.append(row)

    async def commit(self):
        return None


def _capture_rows(monkeypatch) -> list[EmailLog]:
    rows: list[EmailLog] = []
    monkeypatch.setattr(
        email_service, "AsyncSessionFactory", lambda: _CapturingSession(rows)
    )
    monkeypatch.setattr(
        email_service,
        "_send",
        lambda to, subject, html, attachments=None: {
            "status": "sent",
            "resend_id": "stub",
            "error": None,
        },
    )
    return rows


def _length(column) -> int:
    return EmailLog.__table__.c[column].type.length


def test_order_number_fits_any_order_number():
    assert _length("order_number") >= _ORDERS_ORDER_NUMBER_LENGTH


def test_reference_fits_a_uuid():
    assert _length("reference") >= len(str(uuid.uuid4()))


async def test_a_local_first_counter_order_number_is_logged_whole(monkeypatch):
    rows = _capture_rows(monkeypatch)
    order_number = "POS-B001-2026-09-24-T2-0042"

    await email_service._log(
        "counter_pricing_mismatch",
        "owner@example.com",
        "Pricing mismatch",
        {"status": "sent", "resend_id": "stub", "error": None},
        order_number,
    )

    [row] = rows
    assert row.order_number == order_number
    assert row.reference is None


async def test_inventory_report_email_logs_its_id_as_a_reference(monkeypatch):
    rows = _capture_rows(monkeypatch)
    report_id = str(uuid.uuid4())

    await email_service.send_inventory_report_submitted(
        report_id=report_id,
        report_name="Production & Finished Goods - SHJ",
        branch_name="Sharjah Kitchen",
        business_date="2026-09-24",
        submitted_by="Fahim",
        status="posted",
        variance_cost=Decimal("0"),
        variance_lines=[],
    )

    assert len(rows) == len(email_service.INVENTORY_REPORT_RECIPIENTS)
    for row in rows:
        assert row.template == "inventory_report_submitted"
        # Not an order: the admin would link it to a page that does not exist.
        assert row.order_number is None
        assert row.reference == report_id
        assert len(row.reference) <= _length("reference")


async def test_transfer_variance_email_logs_its_reference(monkeypatch):
    rows = _capture_rows(monkeypatch)

    await email_service.send_transfer_sending_variance(
        order_id="00000000-0000-0000-0000-000000000000",
        order_reference="TO-1",
        transfer_reference="TRF-1",
        source_branch_name="Source",
        destination_branch_name="Dest",
        business_date="2026-09-12",
        sent_by="Picker",
        lines=[{"item_name": "Flour", "requested": "10", "sent": "6", "delta": "-4"}],
    )

    assert rows
    assert all(r.order_number is None and r.reference == "TRF-1" for r in rows)


async def test_purchase_order_variance_email_logs_its_reference(monkeypatch):
    rows = _capture_rows(monkeypatch)

    await email_service.send_purchase_order_receiving_variance(
        purchase_order_id="00000000-0000-0000-0000-000000000000",
        purchase_order_reference="PO-003234",
        supplier_name="Supplier",
        branch_name="Sharjah Kitchen",
        business_date="2026-09-24",
        received_by="Receiver",
        lines=[
            {
                "item_name": "Flour",
                "ordered": "10",
                "received": "6",
                "delta": "-4",
            }
        ],
    )

    assert rows
    assert all(r.order_number is None and r.reference == "PO-003234" for r in rows)
