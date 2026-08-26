"""wait_for_otp against a fake IMAP server — no network, no Playwright."""

from __future__ import annotations

import pytest

from aggregator_bootstrap import mailbox
from aggregator_bootstrap.config import settings
from aggregator_bootstrap.mailbox import (
    OTPPollingError,
    extract_otp_from_text,
    wait_for_otp,
)

SAMPLE_EMAIL = (
    b"From: noon <no-reply@noon.com>\r\n"
    b"Subject: Verify your login\r\n"
    b"Date: Tue, 26 Aug 2026 10:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Your one-time verification code is 483920. It expires in 5 minutes.\r\n"
)


class _FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL used as a context manager."""

    def __init__(self, message: bytes = SAMPLE_EMAIL) -> None:
        self._message = message

    def __enter__(self) -> "_FakeIMAP":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def login(self, user: str, password: str) -> tuple[str, list]:
        return ("OK", [b"logged in"])

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list]:
        return ("OK", [b"1"])

    def uid(self, command: str, *args):
        if command == "search":
            return ("OK", [b"1"])
        if command == "fetch":
            return ("OK", [(b"1 (RFC822 {N}", self._message)])
        raise AssertionError(f"unexpected uid command {command!r}")


@pytest.fixture(autouse=True)
def _imap_settings(monkeypatch):
    monkeypatch.setattr(settings, "OTP_IMAP_HOST", "imap.example.com")
    monkeypatch.setattr(settings, "OTP_IMAP_USER", "ops@example.com")
    monkeypatch.setattr(settings, "OTP_IMAP_PASSWORD", "secret")
    monkeypatch.setattr(settings, "OTP_IMAP_FOLDER", "INBOX")


def test_extract_otp_from_text_finds_the_code():
    assert extract_otp_from_text("code: 483920 now") == "483920"
    assert extract_otp_from_text("no digits here") is None


async def test_wait_for_otp_reads_code_from_sample_email(monkeypatch):
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", lambda host, port: _FakeIMAP())
    code = await wait_for_otp(
        sender_filter="noon",
        subject_filter="verify",
        timeout=5,
    )
    assert code == "483920"


async def test_wait_for_otp_filters_by_sender(monkeypatch):
    # A sender the message does not match yields no code -> timeout error.
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", lambda host, port: _FakeIMAP())
    with pytest.raises(OTPPollingError):
        await wait_for_otp(
            sender_filter="talabat",
            subject_filter="verify",
            timeout=0,
            poll_interval=0,
        )
