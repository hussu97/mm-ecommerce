"""Copied addresses reach Resend, and only when there are any (a custom order's
invoice copies the owners; nothing else copies anyone)."""

from __future__ import annotations

import pytest

from app.services import email_service


@pytest.fixture
def resend_params(monkeypatch):
    captured: list[dict] = []

    def fake_send(params):
        captured.append(params)
        return {"id": "re_test"}

    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "re_key")
    monkeypatch.setattr(email_service.resend.Emails, "send", fake_send)
    return captured


def test_copies_travel_with_the_email(resend_params):
    result = email_service._send(
        "customer@example.com",
        "Invoice",
        "<p>hi</p>",
        cc=["owner@example.com", "  ", "not-an-address"],
    )
    assert result["status"] == "sent"
    assert resend_params[0]["cc"] == ["owner@example.com"]


def test_an_email_with_no_copies_sends_the_payload_it_always_did(resend_params):
    email_service._send("customer@example.com", "Hello", "<p>hi</p>")
    assert "cc" not in resend_params[0]


@pytest.mark.asyncio
async def test_an_attachment_with_copies_is_journalled_against_its_order(
    monkeypatch,
):
    sent: list[tuple] = []
    logged: list[dict] = []

    def fake_send(to, subject, html, attachments=None, *, cc=None):
        sent.append((to, attachments[0]["filename"], cc))
        return {"status": "sent", "resend_id": "re_1", "error": None}

    async def fake_log(template, recipient, subject, result, order_number=None, **kw):
        logged.append({"order_number": order_number, **kw})

    monkeypatch.setattr(email_service, "_send", fake_send)
    monkeypatch.setattr(email_service, "_log", fake_log)
    await email_service.send_with_attachment(
        "customer@example.com",
        "Invoice CO-1",
        "<p>attached</p>",
        filename="CO-1.pdf",
        content=b"%PDF",
        template="custom_order_invoice",
        cc=["owner@example.com"],
        order_number="CO-1",
    )
    assert sent == [("customer@example.com", "CO-1.pdf", ["owner@example.com"])]
    assert logged == [{"order_number": "CO-1", "cc": ["owner@example.com"]}]
