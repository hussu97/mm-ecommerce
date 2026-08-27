"""Load and save the encrypted marketplace session.

The seam between the two worlds: a browser bootstrap writes a session here
through `POST /aggregators/session`; the httpx providers read it and replay it;
a restarted worker pulls the Playwright blob back through
`GET /aggregators/worker/sessions` so a deploy is not a re-login. One row per
`(channel, account_ref)`.

Follows the transaction convention: everything here `flush()`es and lets the
request-scoped session commit. The one caller that must persist regardless of
its own outcome — the ingest loop, which owns its session — commits explicitly
and says so at its own call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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


_NOON_SCOPE_KEYS = (
    ("restaurant_code", "n-restaurantcode"),
    ("project", "x-project"),
    ("locale", "x-locale"),
)


def merge_noon_scope_from_extras(session: LoadedSession, extras: dict) -> LoadedSession:
    """Fill missing Noon RMS headers from `aggregator_account.extras`.

    Cookies and Akamai state still come from the capture; brand/project scope
    is documented on the account row (migration 155 / branch map) so a session
    push is not blocked when the first snapshot missed a finance request.
    """
    if not extras:
        return session
    tokens = dict(session.tokens)
    profile = dict(session.header_profile)
    for token_key, header_key in _NOON_SCOPE_KEYS:
        value = extras.get(token_key)
        if not value:
            continue
        if not tokens.get(token_key):
            tokens[token_key] = str(value)
        if header_key not in profile:
            profile[header_key] = str(value)
    if "x-platform" not in profile:
        profile["x-platform"] = "web"
    return replace(session, tokens=tokens, header_profile=profile)


async def enrich_noon_from_account(
    db: AsyncSession, session: LoadedSession | None
) -> LoadedSession | None:
    if session is None or session.channel != "noon":
        return session
    from app.services.aggregators import account_store

    acct = await account_store.load(db, session.channel, session.account_ref)
    if acct is None:
        return session
    return merge_noon_scope_from_extras(session, acct.extras or {})


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
    storage_state: dict | None = None,
    token_expires_at: datetime | None = None,
    cookie_expires_at: datetime | None = None,
) -> AggregatorSession:
    """Store a freshly captured session, replacing whatever was there.

    Called by the bootstrap/warmer push. Sets the row `live` and stamps
    `last_bootstrap_at` — a new login is by definition the freshest the session
    ever gets. `storage_state` is optional: an older worker that only captured
    cookies must not wipe a blob a headed login already stored.
    """
    row = await _row(db, channel, account_ref)
    if row is None:
        row = AggregatorSession(channel=channel, account_ref=account_ref)
        db.add(row)
    row.cookies_encrypted = crypto.encrypt_json(cookies)
    row.tokens_encrypted = crypto.encrypt_json(tokens)
    row.header_profile_encrypted = crypto.encrypt_json(header_profile)
    if storage_state:
        row.storage_state_encrypted = crypto.encrypt_json(storage_state)
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
    """Flag a session the httpx layer can no longer save — only a headed login can.

    The signal the monitoring alerts on and the worker surfaces. Distinct from
    `dead`, which a human sets when a channel is retired.
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


async def list_worker_bundles(db: AsyncSession) -> list[dict]:
    """Decrypted sessions for the worker to hydrate after a deploy/restart.

    Dead rows are omitted — a retired channel should not come back to life on
    the next container start. `needs_bootstrap` rows are still returned: they
    may hold a storage_state that a headed login can resume from, and the
    worker decides whether the probe still authenticates.
    """
    rows = await db.scalars(
        select(AggregatorSession)
        .where(AggregatorSession.status != SESSION_DEAD)
        .order_by(AggregatorSession.channel)
    )
    bundles: list[dict] = []
    for row in rows:
        bundles.append(
            {
                "channel": row.channel,
                "account_ref": row.account_ref,
                "cookies": crypto.decrypt_json(row.cookies_encrypted) or {},
                "tokens": crypto.decrypt_json(row.tokens_encrypted) or {},
                "header_profile": crypto.decrypt_json(row.header_profile_encrypted)
                or {},
                "storage_state": crypto.decrypt_json(row.storage_state_encrypted),
                "token_expires_at": row.token_expires_at,
                "cookie_expires_at": row.cookie_expires_at,
                "status": row.status,
                "last_warmed_at": row.last_warmed_at,
            }
        )
    return bundles
