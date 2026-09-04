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
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aggregator import (
    SESSION_DEAD,
    SESSION_LIVE,
    SESSION_NEEDS_BOOTSTRAP,
    AggregatorBranchMap,
    AggregatorSession,
)
from app.models.base import utcnow

from . import crypto, policy


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
    #: When the worker's heal daemon will next try this channel's login (published
    #: by the worker on a reauth failure). The ingest reads it to avoid waiting on a
    #: reauth the worker will not perform in time.
    reauth_backoff_until: datetime | None = None


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


#: Per-channel identity that lives on the session's tokens but the provider used
#: to hard-code. Each key is copied verbatim from `aggregator_account.extras`
#: onto `session.tokens` (under the same name), and the provider reads it there
#: with its constant as the fallback — so behaviour is identical until an
#: operator populates the account row.
#:   - talabat: `global_entity_id` (e.g. "TB_AE"), the `x-global-entity-id`
#:     header / `globalEntityId` GraphQL arg on every Talabat request.
#:   - careem:  `city_id` (e.g. "1" = Dubai), the `{city}` segment of the
#:     per-outlet orders endpoint.
_TALABAT_EXTRA_TOKEN_KEYS = ("global_entity_id",)
_CAREEM_EXTRA_TOKEN_KEYS = ("city_id",)


def _merge_extra_tokens(
    session: LoadedSession, extras: dict, keys: tuple[str, ...]
) -> LoadedSession:
    """Copy the named `extras` values onto `session.tokens` where the capture
    left them unset.

    Null-safe by construction: an empty extras — or one that names none of
    `keys`, or whose values the session already carries — returns the session
    untouched, so the provider's built-in constant still stands. The same
    account-extras idiom as noon's RMS scope, but tokens-only (these providers
    read identity off `tokens`, not off headers)."""
    if not extras:
        return session
    tokens = dict(session.tokens)
    changed = False
    for key in keys:
        value = extras.get(key)
        if value and not tokens.get(key):
            tokens[key] = str(value)
            changed = True
    return replace(session, tokens=tokens) if changed else session


async def _talabat_outlet_ids(db: AsyncSession) -> list[str]:
    """Active Talabat order-store ids, from the branch map (the canonical config).

    The order export scopes to these `account_ids`. The browser capture used to
    scrape them from the Report Builder store picker, so an automated
    email/OTP re-login — which never opens that page — lands a session with no
    store ids and `fetch_sales` cannot scope an export. The branch map already
    holds them (`external_outlet_id`), one operator-owned source, so injecting
    them here makes ingest survive any re-login instead of silently breaking on
    the next captured session.
    """
    rows = await db.scalars(
        select(AggregatorBranchMap.external_outlet_id).where(
            AggregatorBranchMap.channel == "talabat",
            AggregatorBranchMap.is_active.is_(True),
            AggregatorBranchMap.external_outlet_id.is_not(None),
        )
    )
    # De-dup, preserve a stable order (the export is order-insensitive, but a
    # deterministic list keeps the request byte-identical run to run).
    return sorted({str(r) for r in rows if r})


async def enrich_talabat_from_account(
    db: AsyncSession, session: LoadedSession | None
) -> LoadedSession | None:
    if session is None or session.channel != "talabat":
        return session
    from app.services.aggregators import account_store

    acct = await account_store.load(db, session.channel, session.account_ref)
    extras = acct.extras or {} if acct is not None else {}
    session = _merge_extra_tokens(session, extras, _TALABAT_EXTRA_TOKEN_KEYS)
    # Only reach for the branch map when the captured session carries no store
    # ids of its own — a session that scraped them keeps precedence, and this
    # backfills the automated-login case (see `_talabat_outlet_ids`). Mirrors
    # the provider's `_order_account_ids` key set; kept local to avoid importing
    # the provider (and its httpx stack) into the session store.
    tokens = session.tokens or {}
    has_ids = any(
        isinstance(tokens.get(k), list) and tokens.get(k)
        for k in ("account_ids", "store_ids", "accountIds")
    )
    if not has_ids:
        outlet_ids = await _talabat_outlet_ids(db)
        if outlet_ids:
            merged = dict(tokens)
            merged["account_ids"] = outlet_ids
            session = replace(session, tokens=merged)
    return session


async def enrich_careem_from_account(
    db: AsyncSession, session: LoadedSession | None
) -> LoadedSession | None:
    if session is None or session.channel != "careem":
        return session
    from app.services.aggregators import account_store

    acct = await account_store.load(db, session.channel, session.account_ref)
    if acct is None:
        return session
    return _merge_extra_tokens(session, acct.extras or {}, _CAREEM_EXTRA_TOKEN_KEYS)


async def enrich_session(
    db: AsyncSession, session: LoadedSession | None
) -> LoadedSession | None:
    """Channel-specific session enrichment before a sweep (account extras, etc.)."""
    if session is None:
        return session
    if session.channel == "noon":
        return await enrich_noon_from_account(db, session)
    if session.channel == "talabat":
        return await enrich_talabat_from_account(db, session)
    if session.channel == "careem":
        return await enrich_careem_from_account(db, session)
    return session


def session_unusable_reason(
    session: LoadedSession | None, *, now: datetime | None = None
) -> str | None:
    """Why a loaded session is not safe to replay, or None if it is fine.

    Proactive liveness: the sweep used to check `status == live` only and
    discover a dead session by its 401 mid-run. The stored token/cookie expiries
    were written but never read (`upsert_bootstrap` sets them; nothing consumed
    them). This reads them: a session whose stored expiry has passed is unusable
    *before* the run wastes a pass on it. A NULL expiry is "unknown", so it never
    downgrades a session on its own — only a status that is not live, or an
    expiry that has demonstrably passed, does. Returns a short reason for the
    operator ("needs_bootstrap", "token expired", …) so the trigger can say which
    channels will not run and why.

    The cookie expiry is skipped for channels whose `ChannelPolicy` marks it
    advisory (Talabat), whose anti-bot cookie rotates on replay and outlives its
    short nominal TTL — honouring it there starved the channel of every intraday
    sweep. See `policy.ChannelPolicy.cookie_expiry_advisory`.
    """
    if session is None:
        return "no session — never bootstrapped"
    return unusable_reason_for(
        channel=session.channel,
        status=session.status,
        token_expires_at=session.token_expires_at,
        cookie_expires_at=session.cookie_expires_at,
        now=now,
    )


def unusable_reason_for(
    *,
    channel: str,
    status: str,
    token_expires_at: datetime | None,
    cookie_expires_at: datetime | None,
    now: datetime | None = None,
) -> str | None:
    """`session_unusable_reason` over loose columns, for callers holding a row.

    The single place the advisory-cookie policy is applied. It exists so
    `list_worker_bundles` can publish the same verdict the replay path uses
    without decrypting a blob to build a `LoadedSession` first — and, more to the
    point, so there is exactly ONE implementation of "is this dead". The worker
    kept its own copy of this rule and drifted: it never learned that Talabat's
    cookie expiry is advisory, so it re-logged Talabat in every 15 minutes,
    around the clock, on a session that was fine (96 re-logins in 16.8h of
    production on 2026-09-03, against a 23/day baseline). A second copy of a
    liveness rule is not a style problem; it is that outage.
    """
    if status != SESSION_LIVE:
        return status
    now = now or utcnow()
    checks = [("token", token_expires_at)]
    if not policy.policy_for(channel).cookie_expiry_advisory:
        checks.append(("cookie", cookie_expires_at))
    for label, exp in checks:
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= now:
                return f"{label} expired"
    return None


def is_session_usable(
    session: LoadedSession | None, *, now: datetime | None = None
) -> bool:
    """Whether a loaded session is live and not past a known expiry."""
    return session_unusable_reason(session, now=now) is None


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
        reauth_backoff_until=row.reauth_backoff_until,
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
    # A fresh login means the channel is healthy again — forget any published heal
    # backoff so the ingest does not keep skipping a channel that has recovered.
    row.reauth_backoff_until = None
    await db.flush()
    return row


async def set_reauth_backoff(
    db: AsyncSession,
    channel: str,
    *,
    backoff_until: datetime | None,
    account_ref: str = "",
) -> None:
    """Publish (or clear) when the worker's heal daemon will next re-drive this
    channel's login. Called by the worker on a reauth failure so the ingest can
    skip a reauth wait the worker will not honour in time. Its own committed write
    on a dedicated column — never touches the session blob or status. No-op if the
    row does not exist yet (nothing to skip-wait on)."""
    row = await _row(db, channel, account_ref)
    if row is not None:
        row.reauth_backoff_until = backoff_until
        await db.flush()


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


def _expiry_passed(exp: datetime | None, now: datetime) -> bool:
    """Whether a stored expiry column has elapsed. NULL is unknown, not expired."""
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= now


async def list_heal_channels(db: AsyncSession) -> list[dict]:
    """Channel + status (+ expiry flags from columns). Never decrypts blobs.

    The VM heal cron uses this to decide whether to start a worker at all.
    Selecting only the status/expiry columns keeps the Fernet blobs off the
    wire and out of this path — hydrate remains a separate, decrypting read.
    """
    rows = (
        await db.execute(
            select(
                AggregatorSession.channel,
                AggregatorSession.status,
                AggregatorSession.token_expires_at,
                AggregatorSession.cookie_expires_at,
            ).order_by(AggregatorSession.channel)
        )
    ).all()
    now = utcnow()
    return [
        {
            "channel": channel,
            "status": status,
            "token_expired": _expiry_passed(token_exp, now),
            # Advisory-cookie channels (Talabat) report cookie_expired=False: their
            # PerimeterX cookie's ~5-minute nominal TTL is not a liveness signal (it
            # rotates/outlives it), so keying the 2-minute heal cron on it re-warmed
            # talabat headed every 2 minutes all day, holding the shared warm flock
            # and starving the other channels. Heal talabat on a non-live status or a
            # real token expiry instead — same authority the API sweep now uses.
            "cookie_expired": (
                False
                if policy.policy_for(channel).cookie_expiry_advisory
                else _expiry_passed(cookie_exp, now)
            ),
        }
        for channel, status, token_exp, cookie_exp in rows
    ]


async def list_worker_bundles(db: AsyncSession) -> list[dict]:
    """Decrypted sessions for the worker to hydrate after a deploy/restart.

    Dead rows are omitted — a retired channel should not come back to life on
    the next container start. `needs_bootstrap` rows are still returned: they
    may hold a storage_state that a headed login can resume from, and the
    worker decides whether the probe still authenticates.

    Each bundle carries `unusable_reason` — this API's authoritative verdict on
    whether the session is dead, so the worker's heal loop does not have to
    re-derive it and drift from us again (see `unusable_reason_for`). `None`
    means healthy; the worker re-logs in only when it is set.
    """
    rows = await db.scalars(
        select(AggregatorSession)
        .where(AggregatorSession.status != SESSION_DEAD)
        .order_by(AggregatorSession.channel)
    )
    now = utcnow()  # one clock for every row's verdict
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
                "unusable_reason": unusable_reason_for(
                    channel=row.channel,
                    status=row.status,
                    token_expires_at=row.token_expires_at,
                    cookie_expires_at=row.cookie_expires_at,
                    now=now,
                ),
            }
        )
    return bundles
