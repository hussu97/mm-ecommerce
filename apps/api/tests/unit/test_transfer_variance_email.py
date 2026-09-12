"""The transfer sending-variance email fires only when there is a variance, and
renders its template when there is one. DB-free: ``_send`` and ``_log`` are
stubbed so nothing leaves the process and no session is opened."""

from __future__ import annotations

import pytest

from app.services import email_service

pytestmark = pytest.mark.asyncio


def _stub_send_and_log(monkeypatch):
    sent: list[dict] = []

    def fake_send(to, subject, html, attachments=None):
        sent.append({"to": to, "subject": subject, "html": html})
        return {"status": "sent", "resend_id": "stub", "error": None}

    async def fake_log(*args, **kwargs):
        return None

    monkeypatch.setattr(email_service, "_send", fake_send)
    monkeypatch.setattr(email_service, "_log", fake_log)
    return sent


async def test_no_email_when_there_is_no_variance(monkeypatch):
    sent = _stub_send_and_log(monkeypatch)
    await email_service.send_transfer_sending_variance(
        order_id="00000000-0000-0000-0000-000000000000",
        order_reference="TO-1",
        transfer_reference="TRF-1",
        source_branch_name="Source",
        destination_branch_name="Dest",
        business_date="2026-09-12",
        sent_by="Picker",
        lines=[],
    )
    assert sent == []


async def test_email_sent_and_renders_when_variance_present(monkeypatch):
    sent = _stub_send_and_log(monkeypatch)
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
    assert len(sent) == len(email_service.TRANSFER_VARIANCE_RECIPIENTS)
    message = sent[0]
    assert message["to"] in email_service.TRANSFER_VARIANCE_RECIPIENTS
    assert "TRF-1" in message["subject"]
    # The variance line reached the rendered body.
    assert "Flour" in message["html"]
    assert "-4" in message["html"]
