"""Read an email OTP from the configured IMAP mailbox.

Some channels (Noon RMS, Talabat) gate a fresh login behind a one-time code
mailed to the operator address. The login flows in `channels/login.py` request
the code, then await `wait_for_otp` to pull it back out of the inbox so the run
stays unattended.

`imaplib` is blocking stdlib, so the actual IMAP poll runs in a worker thread
(`asyncio.to_thread`) and `wait_for_otp` polls it until the code lands or the
timeout elapses. Nothing here imports Playwright or any third-party dependency,
so it stays importable — and unit-testable against a fake IMAP server — without
the browser library installed.
"""

from __future__ import annotations

import asyncio
import imaplib
import re
import time
from datetime import UTC, datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from .config import settings

#: A 4–8 digit run is the OTP. Kept identical to the standalone scraper's rule.
DEFAULT_OTP_PATTERN = re.compile(r"\b(\d{4,8})\b")


class OTPPollingError(RuntimeError):
    """No OTP matching the filters arrived before the timeout."""


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def extract_otp_from_text(
    text: str, *, otp_pattern: re.Pattern[str] = DEFAULT_OTP_PATTERN
) -> str | None:
    """Pull the first 4–8 digit code out of a decoded email body."""
    match = otp_pattern.search(text)
    return match.group(1) if match else None


def _message_text(message: Message) -> str:
    """Flatten a (possibly multipart) message to plain text, stripping HTML."""
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if content_type == "text/html":
                decoded = re.sub(r"<[^>]+>", " ", decoded)
            parts.append(decoded)
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(parts)


def _message_received_at(message: Message) -> datetime | None:
    raw_date = message.get("Date")
    if not raw_date:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_matches(
    *, subject: str, sender: str, sender_filter: str | None, subject_filter: str | None
) -> bool:
    effective_sender = (sender_filter or "").strip().lower()
    effective_subject = (subject_filter or "").strip().lower()
    if effective_sender and effective_sender not in sender.lower():
        return False
    if effective_subject and effective_subject not in subject.lower():
        return False
    return True


def _fetch_latest_otp(
    *,
    sender_filter: str | None,
    subject_filter: str | None,
    since: datetime | None,
    otp_pattern: re.Pattern[str] = DEFAULT_OTP_PATTERN,
    max_messages: int = 25,
) -> str | None:
    """One blocking IMAP sweep. Returns the newest matching code, or None.

    Reads connection settings from `OTP_IMAP_*`. Messages older than `since`
    (by their Date header) are skipped so a stale code from a previous run is
    never mistaken for the one we just requested.
    """
    if not settings.OTP_IMAP_HOST:
        raise OTPPollingError("OTP_IMAP_HOST is not configured.")
    if not settings.OTP_IMAP_USER or not settings.OTP_IMAP_PASSWORD:
        raise OTPPollingError("OTP_IMAP_USER / OTP_IMAP_PASSWORD are not configured.")

    with imaplib.IMAP4_SSL(settings.OTP_IMAP_HOST, settings.OTP_IMAP_PORT) as client:
        client.login(settings.OTP_IMAP_USER, settings.OTP_IMAP_PASSWORD)
        status, _ = client.select(settings.OTP_IMAP_FOLDER, readonly=True)
        if status != "OK":
            raise OTPPollingError(
                f"Unable to open IMAP folder {settings.OTP_IMAP_FOLDER!r}."
            )
        status, data = client.uid("search", None, "ALL")
        if status != "OK":
            raise OTPPollingError("Unable to search IMAP mailbox.")
        raw_ids = data[0].split() if data and data[0] else []
        candidate_ids = [item.decode() for item in raw_ids][-max_messages:]
        for uid in reversed(candidate_ids):
            fetch_status, payload = client.uid("fetch", uid, "(RFC822)")
            if fetch_status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = message_from_bytes(payload[0][1])
            subject = _decode_header_value(message.get("Subject"))
            sender = _decode_header_value(message.get("From"))
            if not _message_matches(
                subject=subject,
                sender=sender,
                sender_filter=sender_filter,
                subject_filter=subject_filter,
            ):
                continue
            received_at = _message_received_at(message)
            if since is not None and received_at is not None and received_at < since:
                continue
            code = extract_otp_from_text(
                _message_text(message), otp_pattern=otp_pattern
            )
            if code:
                return code
    return None


async def wait_for_otp(
    *,
    sender_filter: str | None,
    subject_filter: str | None,
    since: datetime | None = None,
    timeout: float = 120.0,
    poll_interval: float = 5.0,
    otp_pattern: re.Pattern[str] = DEFAULT_OTP_PATTERN,
) -> str:
    """Poll the IMAP mailbox until a matching OTP arrives (or `timeout` elapses).

    The blocking IMAP work runs in a worker thread so the browser's event loop
    keeps turning. Raises `OTPPollingError` if nothing matches in time.
    """
    deadline = time.monotonic() + timeout
    while True:
        code = await asyncio.to_thread(
            _fetch_latest_otp,
            sender_filter=sender_filter,
            subject_filter=subject_filter,
            since=since,
            otp_pattern=otp_pattern,
        )
        if code:
            return code
        if time.monotonic() >= deadline:
            raise OTPPollingError(
                "Timed out waiting for an OTP email "
                f"(sender~={sender_filter!r}, subject~={subject_filter!r})."
            )
        await asyncio.sleep(poll_interval)
