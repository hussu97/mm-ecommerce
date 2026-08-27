"""Load and save the encrypted marketplace login recipe.

`aggregator_account` is the durable half of aggregator auth: which flow to
run, the Fernet-sealed portal email/password, and (when the flow needs an
OTP) the Fernet-sealed IMAP mailbox the worker reads the code from. Distinct
from `session_store`, which holds the *derived* cookie jar. Follows the
transaction convention — `flush()` here, the request-scoped session commits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.aggregator import (
    CHANNEL_LOGIN_METHODS,
    LOGIN_METHODS,
    METHODS_NEED_EMAIL,
    METHODS_NEED_PASSWORD,
    AggregatorAccount,
    method_needs_otp,
)

from . import crypto

_MAILBOX_KEYS = (
    "host",
    "port",
    "username",
    "password",
    "folder",
    "sender_filter",
    "subject_filter",
)


@dataclass
class LoadedAccount:
    """A decrypted login recipe, ready for the worker to drive a portal."""

    channel: str
    account_ref: str = ""
    login_method: str = ""
    email: str = ""
    password: str = ""
    mailbox: dict[str, Any] = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    updated_at: datetime | None = None


def merge_credentials(
    existing: dict | None,
    *,
    email: str | None,
    password: str | None,
) -> dict[str, str]:
    """Keep the stored password when a PUT omits it.

    A rotation of the email without re-typing the password must not wipe the
    secret. A first insert with no password is refused by the caller when the
    method needs one.
    """
    out: dict[str, str] = {}
    if existing:
        if existing.get("email"):
            out["email"] = str(existing["email"])
        if existing.get("password"):
            out["password"] = str(existing["password"])
    if email is not None:
        out["email"] = email.strip()
    if password is not None:
        out["password"] = password
    return out


def merge_mailbox(
    existing: dict | None,
    incoming: dict | None,
    *,
    clear: bool = False,
) -> dict[str, Any] | None:
    """Keep the stored IMAP password when a PUT omits it.

    `clear` drops the mailbox entirely (OTP still required, but the worker
    falls back to env until a new mailbox is saved). An empty incoming dict
    with no existing row is stored as None.
    """
    if clear:
        return None
    if incoming is None:
        return dict(existing) if existing else None
    out: dict[str, Any] = dict(existing or {})
    for key in _MAILBOX_KEYS:
        if key not in incoming or incoming[key] is None:
            continue
        if key == "password" and incoming[key] == "":
            continue
        out[key] = incoming[key]
    if out.get("port") is not None:
        try:
            out["port"] = int(out["port"])
        except (TypeError, ValueError) as exc:
            raise BadRequestError("mailbox port must be an integer") from exc
    if not (out.get("host") or out.get("username")):
        return None
    return out


def _mailbox_public(mailbox: dict[str, Any] | None) -> dict | None:
    if not mailbox:
        return None
    return {
        "host": str(mailbox.get("host") or ""),
        "port": int(mailbox.get("port") or 993),
        "username": str(mailbox.get("username") or ""),
        "folder": str(mailbox.get("folder") or "INBOX"),
        "sender_filter": str(mailbox.get("sender_filter") or ""),
        "subject_filter": str(mailbox.get("subject_filter") or ""),
        "has_password": bool(mailbox.get("password")),
    }


def public_view(account: LoadedAccount) -> dict:
    """Admin health shape — emails and method, never a password."""
    mailbox = _mailbox_public(account.mailbox)
    return {
        "channel": account.channel,
        "account_ref": account.account_ref,
        "login_method": account.login_method,
        "otp_required": method_needs_otp(account.login_method),
        "email": account.email,
        "has_password": bool(account.password),
        "has_mailbox": bool(
            mailbox and (mailbox.get("host") or mailbox.get("username"))
        ),
        "mailbox": mailbox,
        "extras": account.extras or {},
        "updated_at": account.updated_at,
    }


def worker_view(account: LoadedAccount) -> dict:
    """Full recipe for the worker bearer. Secrets on the wire, on purpose."""
    return {
        "channel": account.channel,
        "account_ref": account.account_ref,
        "login_method": account.login_method,
        "otp_required": method_needs_otp(account.login_method),
        "email": account.email,
        "password": account.password,
        "mailbox": dict(account.mailbox or {}),
        "extras": account.extras or {},
    }


def _opened(row: AggregatorAccount) -> LoadedAccount:
    creds = crypto.decrypt_json(row.credentials_encrypted) or {}
    mailbox = crypto.decrypt_json(row.mailbox_encrypted) or {}
    return LoadedAccount(
        channel=row.channel,
        account_ref=row.account_ref,
        login_method=row.login_method,
        email=str(creds.get("email") or ""),
        password=str(creds.get("password") or ""),
        mailbox=dict(mailbox),
        extras=dict(row.extras or {}),
        updated_at=row.updated_at,
    )


async def _row(
    db: AsyncSession, channel: str, account_ref: str = ""
) -> AggregatorAccount | None:
    return await db.scalar(
        select(AggregatorAccount).where(
            AggregatorAccount.channel == channel,
            AggregatorAccount.account_ref == account_ref,
        )
    )


async def load(
    db: AsyncSession, channel: str, account_ref: str = ""
) -> LoadedAccount | None:
    row = await _row(db, channel, account_ref)
    if row is None:
        return None
    return _opened(row)


async def upsert(
    db: AsyncSession,
    *,
    channel: str,
    account_ref: str = "",
    login_method: str | None = None,
    email: str | None = None,
    password: str | None = None,
    mailbox: dict | None = None,
    clear_mailbox: bool = False,
    extras: dict | None = None,
) -> AggregatorAccount:
    """Create or update one account recipe."""
    method = login_method or CHANNEL_LOGIN_METHODS.get(channel)
    if method not in LOGIN_METHODS:
        raise BadRequestError(f"unknown aggregator login method: {login_method}")
    row = await _row(db, channel, account_ref)
    existing_creds = (
        crypto.decrypt_json(row.credentials_encrypted) if row is not None else None
    )
    existing_mailbox = (
        crypto.decrypt_json(row.mailbox_encrypted) if row is not None else None
    )
    creds = merge_credentials(existing_creds, email=email, password=password)
    if method in METHODS_NEED_EMAIL and not creds.get("email"):
        raise BadRequestError(
            "aggregator account needs a portal email for this login method"
        )
    if method in METHODS_NEED_PASSWORD and not creds.get("password"):
        raise BadRequestError(
            "aggregator account needs a portal password for this login method "
            "(omit password on later saves to keep the stored one)"
        )
    sealed_mailbox = merge_mailbox(existing_mailbox, mailbox, clear=clear_mailbox)
    if row is None:
        row = AggregatorAccount(channel=channel, account_ref=account_ref)
        db.add(row)
    row.login_method = method
    row.credentials_encrypted = crypto.encrypt_json(creds) if creds else None
    row.mailbox_encrypted = crypto.encrypt_json(sealed_mailbox)
    if extras is not None:
        row.extras = extras
    await db.flush()
    return row


async def list_public(db: AsyncSession) -> list[dict]:
    """Admin read: method + email + mailbox host, no secrets."""
    rows = await db.scalars(
        select(AggregatorAccount).order_by(AggregatorAccount.channel)
    )
    return [public_view(_opened(row)) for row in rows]


async def list_worker(db: AsyncSession) -> list[dict]:
    """Worker read: decrypted recipes, authenticated with the push bearer."""
    rows = await db.scalars(
        select(AggregatorAccount).order_by(AggregatorAccount.channel)
    )
    return [worker_view(_opened(row)) for row in rows]
