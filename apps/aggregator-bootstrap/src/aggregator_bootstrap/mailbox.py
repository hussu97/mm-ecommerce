"""Read an email OTP from the per-channel mailbox recipe.

Some channels (Noon RMS, Talabat) gate a fresh login behind a one-time code
mailed to the operator address. The login flows in `channels/login.py` request
the code, then await `wait_for_otp` to pull it back out of that aggregator's
inbox so the run stays unattended.

Preferred path is Microsoft Graph: each aggregator stores its own Azure app
(client id + secret) plus a refresh token on `aggregator_account`. IMAP is
the fallback. Blocking I/O runs in a worker thread (`asyncio.to_thread`).
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
import re
import time
from datetime import UTC, datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from .config import settings

logger = logging.getLogger(__name__)

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


def uses_graph(mailbox: dict | None) -> bool:
    """True when this recipe should read OTP via Microsoft Graph, not IMAP."""
    box = mailbox or {}
    provider = str(box.get("provider") or "").strip().lower()
    if provider == "graph":
        return True
    if provider == "imap":
        return False
    return bool(box.get("refresh_token"))


def _fetch_latest_otp(
    *,
    sender_filter: str | None,
    subject_filter: str | None,
    since: datetime | None,
    otp_pattern: re.Pattern[str] = DEFAULT_OTP_PATTERN,
    max_messages: int = 25,
    mailbox: dict | None = None,
) -> str | None:
    """One blocking sweep. Returns the newest matching code, or None.

    Graph (Hotmail via the Azure app) wins when the recipe says so; otherwise
    IMAP. Prefers the per-channel `mailbox` dict from `aggregator_account`.
    """
    box = mailbox or {}
    if uses_graph(box):
        from .graph_mail import GraphMailboxError
        from .graph_mail import fetch_latest_otp as graph_otp

        token = str(box.get("refresh_token") or "").strip()
        if not token:
            raise OTPPollingError(
                "Microsoft Graph mailbox is selected but not connected. "
                "Run: aggregator-bootstrap mailbox-auth --channel <channel>"
            )
        try:
            code, rotated = graph_otp(
                mailbox=box,
                refresh_token=token,
                sender_filter=sender_filter
                or str(box.get("sender_filter") or "")
                or None,
                subject_filter=subject_filter
                or str(box.get("subject_filter") or "")
                or None,
                since=since,
                otp_pattern=otp_pattern,
                max_messages=max_messages,
            )
        except GraphMailboxError as exc:
            raise OTPPollingError(str(exc)) from exc
        if rotated:
            box["refresh_token"] = rotated
        return code

    # IMAP path — host/user/password from the recipe, else OTP_IMAP_* env.
    effective_sender = sender_filter or str(box.get("sender_filter") or "") or None
    effective_subject = subject_filter or str(box.get("subject_filter") or "") or None
    host = str(box.get("host") or settings.OTP_IMAP_HOST or "").strip()
    try:
        port = int(box.get("port") or settings.OTP_IMAP_PORT or 993)
    except (TypeError, ValueError):
        port = 993
    user = str(box.get("username") or settings.OTP_IMAP_USER or "").strip()
    password = str(box.get("password") or settings.OTP_IMAP_PASSWORD or "")
    folder = (
        str(box.get("folder") or settings.OTP_IMAP_FOLDER or "INBOX").strip() or "INBOX"
    )
    if not host:
        raise OTPPollingError(
            "no IMAP host: store a mailbox on the aggregator login recipe, "
            "or set OTP_IMAP_HOST."
        )
    if not user or not password:
        raise OTPPollingError(
            "IMAP username/password are missing. Store them on the aggregator "
            "login recipe, or set OTP_IMAP_USER / OTP_IMAP_PASSWORD."
        )

    with imaplib.IMAP4_SSL(host, port) as client:
        client.login(user, password)
        status, _ = client.select(folder, readonly=True)
        if status != "OK":
            raise OTPPollingError(f"Unable to open IMAP folder {folder!r}.")
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
                sender_filter=effective_sender,
                subject_filter=effective_subject,
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


async def _persist_rotated_refresh(channel: str, refresh_token: str) -> None:
    """Write a rotated Graph refresh token back onto that channel's recipe."""
    from .accounts import push_account

    try:
        await push_account(
            {
                "channel": channel,
                "mailbox": {
                    "provider": "graph",
                    "refresh_token": refresh_token,
                },
            }
        )
    except Exception:  # noqa: BLE001 — OTP already succeeded; log and continue
        logger.warning(
            "%s: Graph rotated the refresh token but storing it failed",
            channel,
            exc_info=True,
        )


async def wait_for_otp(
    *,
    sender_filter: str | None,
    subject_filter: str | None,
    since: datetime | None = None,
    timeout: float = 120.0,
    poll_interval: float = 5.0,
    otp_pattern: re.Pattern[str] = DEFAULT_OTP_PATTERN,
    mailbox: dict | None = None,
    channel: str | None = None,
) -> str:
    """Poll Graph or IMAP until a matching OTP arrives (or `timeout` elapses).

    `mailbox` is the per-channel recipe from `aggregator_account`. Graph
    (`provider=graph` + refresh token) is preferred when configured; otherwise
    IMAP / `OTP_IMAP_*`. The blocking work runs in a worker thread so the
    browser's event loop keeps turning. Raises `OTPPollingError` if nothing
    matches in time.
    """
    original_refresh = str((mailbox or {}).get("refresh_token") or "")
    deadline = time.monotonic() + timeout
    while True:
        code = await asyncio.to_thread(
            _fetch_latest_otp,
            sender_filter=sender_filter,
            subject_filter=subject_filter,
            since=since,
            otp_pattern=otp_pattern,
            mailbox=mailbox,
        )
        if code:
            rotated = str((mailbox or {}).get("refresh_token") or "")
            if channel and rotated and rotated != original_refresh:
                await _persist_rotated_refresh(channel, rotated)
            return code
        if time.monotonic() >= deadline:
            raise OTPPollingError(
                "Timed out waiting for an OTP email "
                f"(sender~={sender_filter!r}, subject~={subject_filter!r})."
            )
        await asyncio.sleep(poll_interval)
