"""Load and save the encrypted marketplace session.

The seam between the two worlds: a browser bootstrap (which can solve OTP and
run an anti-bot sensor) writes a session here through `POST /aggregators/session`;
the httpx providers read it and replay it, and the token warmer writes a
refreshed token back. One row per `(channel, account_ref)`.

Follows the transaction convention: everything here `flush()`es and lets the
request-scoped session commit. The one caller that must persist regardless of
its own outcome — the ingest loop, which owns its session — commits explicitly
and says so at its own call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aggregator import (
    SESSION_DEAD,
    SESSION_LIVE,
    SESSION_NEEDS_BOOTSTRAP,
    AggregatorSession,
)
from app.models.base import utcnow

from . import crypto


@dataclass
class LoadedSession:
    """A decrypted session, ready for a provider to replay."""

    channel: str
    account_ref: str
    #: name → value, replayed as the request `Cookie`. Carries the load-bearing
    #: anti-bot cookie (`_px3`, `bm_sv`, `WEBDFPID`) alongside the auth cookies.
    cookies: dict[str, str] = field(default_factory=dict)
    #: bearer / refresh / device tokens, channel-shaped.
    tokens: dict = field(default_factory=dict)
    #: The exact UA / client-hint / custom-header set the browser sent, so the
    #: request fingerprint matches the cookie that minted it.
    header_profile: dict[str, str] = field(default_factory=dict)
    token_expires_at: datetime | None = None
    cookie_expires_at: datetime | None = None
    status: str = SESSION_NEEDS_BOOTSTRAP


async def _row(
    db: AsyncSession, channel: str, account_ref: str = ""
) -> AggregatorSession | None:
    return await db.scalar(
        select(AggregatorSession).where(
            AggregatorSession.channel == channel,
            AggregatorSession.account_ref == account_ref,
        )
    )


async def load(
    db: AsyncSession, channel: str, account_ref: str = ""
) -> LoadedSession | None:
    """The live session for a channel, decrypted, or None if never bootstrapped."""
    row = await _row(db, channel, account_ref)
    if row is None:
        return None
    return LoadedSession(
        channel=row.channel,
        account_ref=row.account_ref,
        cookies=crypto.decrypt_json(row.cookies_encrypted) or {},
        tokens=crypto.decrypt_json(row.tokens_encrypted) or {},
        header_profile=crypto.decrypt_json(row.header_profile_encrypted) or {},
        token_expires_at=row.token_expires_at,
        cookie_expires_at=row.cookie_expires_at,
        status=row.status,
    )


async def upsert_bootstrap(
    db: AsyncSession,
    *,
    channel: str,
    account_ref: str = "",
    cookies: dict[str, str],
    tokens: dict,
    header_profile: dict[str, str],
    token_expires_at: datetime | None = None,
    cookie_expires_at: datetime | None = None,
) -> AggregatorSession:
    """Store a freshly captured session, replacing whatever was there.

    Called by the bootstrap/warmer push. Sets the row `live` and stamps
    `last_bootstrap_at` — a new login is by definition the freshest the session
    ever gets.
    """
    row = await _row(db, channel, account_ref)
    if row is None:
        row = AggregatorSession(channel=channel, account_ref=account_ref)
        db.add(row)
    row.cookies_encrypted = crypto.encrypt_json(cookies)
    row.tokens_encrypted = crypto.encrypt_json(tokens)
    row.header_profile_encrypted = crypto.encrypt_json(header_profile)
    row.token_expires_at = token_expires_at
    row.cookie_expires_at = cookie_expires_at
    row.status = SESSION_LIVE
    row.last_bootstrap_at = utcnow()
    row.last_warmed_at = utcnow()
    row.last_error = None
    await db.flush()
    return row


async def record_refresh(
    db: AsyncSession,
    channel: str,
    account_ref: str = "",
    *,
    tokens: dict | None = None,
    token_expires_at: datetime | None = None,
) -> None:
    """Persist a token the warmer refreshed over the API, keeping the session live.

    Rotated refresh tokens land here too — a channel that hands back a new
    refresh token on each use has that new one written or the chain breaks.
    """
    row = await _row(db, channel, account_ref)
    if row is None:
        return
    if tokens is not None:
        row.tokens_encrypted = crypto.encrypt_json(tokens)
    row.token_expires_at = token_expires_at
    row.last_warmed_at = utcnow()
    if row.status == SESSION_NEEDS_BOOTSTRAP:
        row.status = SESSION_LIVE
    row.last_error = None
    await db.flush()


async def record_success(db: AsyncSession, channel: str, account_ref: str = "") -> None:
    """Stamp a clean data pull, clearing any prior error."""
    row = await _row(db, channel, account_ref)
    if row is None:
        return
    row.last_success_at = utcnow()
    row.status = SESSION_LIVE
    row.last_error = None
    await db.flush()


async def mark_needs_bootstrap(
    db: AsyncSession, channel: str, account_ref: str = "", *, error: str
) -> None:
    """Flag a session the httpx layer can no longer save — only a browser can.

    The signal the monitoring alerts on and the warmer/bootstrap worker picks
    up. Distinct from `dead`, which a human sets when a channel is retired.
    """
    row = await _row(db, channel, account_ref)
    if row is None:
        return
    row.status = SESSION_NEEDS_BOOTSTRAP
    row.last_error = error[:2000]
    await db.flush()


async def mark_dead(
    db: AsyncSession, channel: str, account_ref: str = "", *, error: str
) -> None:
    """Retire a session outright — the loop stops touching this channel."""
    row = await _row(db, channel, account_ref)
    if row is None:
        return
    row.status = SESSION_DEAD
    row.last_error = error[:2000]
    await db.flush()
