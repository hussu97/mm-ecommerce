"""The admin-console review surface for shift inventory reports:

- an approver may correct a report only while it is awaiting approval, and the
  correction leaves it awaiting approval (approval, separately, is what posts);
- the submission notification renders with a working link to the admin console.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.inventory_v2 import ShiftInventoryReportStatusEnum
from app.schemas.inventory_v2 import ReportSaveRequest
from app.services import email_service
from app.services.inventory import report_service


def _save_request() -> ReportSaveRequest:
    return ReportSaveRequest(
        idempotency_key="admin-edit-key",
        base_posting_sequence=None,
        notes="corrected the flour count",
        lines=[],
    )


@pytest.mark.asyncio
async def test_edit_pending_report_rejects_a_report_not_awaiting_approval(monkeypatch):
    report = SimpleNamespace(
        id=uuid4(),
        status=ShiftInventoryReportStatusEnum.POSTED.value,
        notes=None,
    )
    monkeypatch.setattr(report_service, "_lock_report", AsyncMock(return_value=report))
    write = AsyncMock()
    monkeypatch.setattr(report_service, "_write_line_edits", write)
    db = SimpleNamespace(flush=AsyncMock())

    with pytest.raises(ConflictError):
        await report_service.edit_pending_report(
            db, report=report, data=_save_request(), user=SimpleNamespace(id=uuid4())
        )
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_pending_report_applies_edits_and_stays_pending(monkeypatch):
    report = SimpleNamespace(
        id=uuid4(),
        status=ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value,
        notes=None,
    )
    monkeypatch.setattr(report_service, "_lock_report", AsyncMock(return_value=report))
    write = AsyncMock()
    monkeypatch.setattr(report_service, "_write_line_edits", write)
    db = SimpleNamespace(flush=AsyncMock())

    result = await report_service.edit_pending_report(
        db, report=report, data=_save_request(), user=SimpleNamespace(id=uuid4())
    )

    write.assert_awaited_once()
    # A correction is not an approval: the report is still waiting for one.
    assert result.status == ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value
    assert result.notes == "corrected the flour count"


def test_admin_report_url_points_at_the_console_detail_page():
    report_id = str(uuid4())
    assert email_service._admin_report_url(report_id) == (
        f"{settings.ADMIN_URL.rstrip('/')}/inventory/reports/{report_id}"
    )


def test_submission_email_renders_with_a_link_to_the_report():
    html = email_service._render(
        "inventory_report_submitted.html",
        recipient_email="office@example.com",
        locale="en",
        report_name="Finished goods closing count",
        branch_name="Sharjah",
        business_date="2026-09-07",
        submitted_by="Jane Cashier",
        status="approved",
        variance_cost="12.50",
        variance_lines=[],
        admin_report_url="https://admin.example.com/inventory/reports/abc-123",
    )
    assert "Finished goods closing count" in html
    assert "Sharjah" in html
    assert "https://admin.example.com/inventory/reports/abc-123" in html
    # Every report auto-posts now, so the mail says posted, never "waiting".
    assert "posted to the stock ledger" in html
    assert "no variances" in html.lower()


def test_submission_email_lists_the_items_with_variance():
    """The variance summary the office reviews after the fact renders as a table
    of the discrepant lines."""
    html = email_service._render(
        "inventory_report_submitted.html",
        recipient_email="office@example.com",
        locale="en",
        report_name="Raw materials closing count",
        branch_name="Sharjah",
        business_date="2026-09-07",
        submitted_by="Jane Cashier",
        status="approved",
        variance_cost="42.00",
        variance_lines=[
            {
                "item_name": "Flour 25kg",
                "unit": "bag",
                "expected": "100",
                "counted": "95",
                "variance": "-5",
                "variance_cost": "42.00",
            }
        ],
        admin_report_url="https://admin.example.com/inventory/reports/abc-123",
    )
    assert "Items with variance" in html
    assert "Flour 25kg" in html
    assert "-5" in html
    assert "42.00" in html


def test_variance_summary_keeps_only_counted_lines_that_differ():
    """Only physical-count lines with a non-zero variance are summarised, with
    the sign preserved and quantities trimmed of trailing zeros."""
    report = SimpleNamespace(
        lines=[
            SimpleNamespace(
                item_id=uuid4(),
                unit="bag",
                expected_quantity="100",
                entered_quantity="95",
                variance_quantity="-5",
                variance_cost="42",
                source_summary={
                    "required_input": "physical_count",
                    "item_name": "Flour 25kg",
                },
            ),
            # Counted exactly — no variance, so it is omitted.
            SimpleNamespace(
                item_id=uuid4(),
                unit="kg",
                expected_quantity="10",
                entered_quantity="10",
                variance_quantity="0",
                variance_cost="0",
                source_summary={
                    "required_input": "physical_count",
                    "item_name": "Sugar",
                },
            ),
            # A movement input posts a figure, it does not reconcile — excluded
            # even though it carries a variance value.
            SimpleNamespace(
                item_id=uuid4(),
                unit="unit",
                expected_quantity="0",
                entered_quantity="3",
                variance_quantity="3",
                variance_cost="9",
                source_summary={
                    "required_input": "receipt",
                    "item_name": "Boxes",
                },
            ),
        ]
    )

    summary = report_service._variance_summary(report)

    assert len(summary) == 1
    row = summary[0]
    assert row["item_name"] == "Flour 25kg"
    assert row["expected"] == "100"
    assert row["counted"] == "95"
    assert row["variance"] == "-5"
    assert row["variance_cost"] == "42.00"


@pytest.mark.asyncio
async def test_submit_report_auto_posts_even_with_a_large_variance(monkeypatch):
    """A report is approved and posted on submit regardless of variance — it is
    never parked in PENDING_APPROVAL waiting for a human."""
    report = SimpleNamespace(
        id=uuid4(),
        branch_id=uuid4(),
        status=ShiftInventoryReportStatusEnum.DRAFT.value,
        base_posting_sequence=None,
        submitted_by=None,
        submitted_at=None,
        template_snapshot={"report_type": "raw_materials"},
        lines=[
            SimpleNamespace(
                confirmed=True,
                entered_quantity=1,
                variance_quantity="900",
                variance_cost="9000",
                source_summary={"required_input": "physical_count"},
            )
        ],
    )
    monkeypatch.setattr(report_service, "_lock_report", AsyncMock(return_value=report))
    monkeypatch.setattr(
        report_service.source_event_service,
        "lock_branch_inventory",
        AsyncMock(),
    )
    monkeypatch.setattr(
        report_service, "_competing_movement_since", AsyncMock(return_value=False)
    )
    post = AsyncMock()
    monkeypatch.setattr(report_service, "post_report", post)
    notify = AsyncMock()
    monkeypatch.setattr(report_service, "_notify_report_submitted", notify)
    db = SimpleNamespace(flush=AsyncMock())
    user = SimpleNamespace(id=uuid4())

    result = await report_service.submit_report(db, report=report, user=user)

    assert result.status == ShiftInventoryReportStatusEnum.APPROVED.value
    post.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_comment_snapshots_author_name_and_trims_body():
    """A reviewer note snapshots the author's name (so it survives the user row
    being deleted) and stores a trimmed body."""
    report = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4(), display_name="Aisha Khan", email="a@x.com")
    added: list = []
    db = SimpleNamespace(add=added.append, flush=AsyncMock())

    comment = await report_service.add_comment(
        db, report=report, user=user, body="  recount the flour  "
    )

    assert added == [comment]
    assert comment.report_id == report.id
    assert comment.author_id == user.id
    assert comment.author_name == "Aisha Khan"
    assert comment.body == "recount the flour"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_comment_falls_back_to_email_without_a_display_name():
    report = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4(), display_name=None, email="ops@x.com")
    db = SimpleNamespace(add=lambda _o: None, flush=AsyncMock())

    comment = await report_service.add_comment(db, report=report, user=user, body="ok")

    assert comment.author_name == "ops@x.com"
