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
        status="pending approval",
        requires_approval=True,
        variance_cost="12.50",
        admin_report_url="https://admin.example.com/inventory/reports/abc-123",
    )
    assert "Finished goods closing count" in html
    assert "Sharjah" in html
    assert "https://admin.example.com/inventory/reports/abc-123" in html


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
