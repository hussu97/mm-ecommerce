"""
Telling the storefront's caches that what they hold has changed.

Two layers hold a copy of what a shopper is shown: Redis here (the read-through
caches in front of products, categories, the advertised coupon, CMS, blog,
translations) and the Next data cache on Vercel, whose fetches carry the tags
`catalogue`, `promo`, `cms`, `blog` and `i18n`. Until this module the second
layer was only ever cleared by its own TTL — a minute — so 99% of the API's
`/products/{slug}` and `/promo-codes/featured` traffic was Vercel re-asking a
question whose answer had not moved.

**Seeing a write.** The same SQLAlchemy session events `counter_bundle_cache`
uses: every committed ORM write — a unit-of-work flush, or a bulk
`update(...)`/`insert(...)`/`delete(...)` through the session — to a table in
`TABLE_TAGS` marks its tags on the session, and the outermost commit purges
them. Nothing has to remember to call this: the console, the register's stock
and busy-mode toggles, the auto-availability sweep, a bulk import, a script on
the ORM and the startup i18n seed are all covered by being writes. A rollback
purges nothing. A bulk UPDATE/DELETE that matched no row purges nothing either
(`promote` decrements stock on every aggregator line with an
`is_stock_product` guard that matches nothing in a shop with no stock
products — purging the catalogue for each of those would be noise).

**Seeing time pass.** Some of what the storefront shows changes with no write
at all: a stockout marked "for an hour" or "until end of day" is read as over
the moment `out_of_stock_until` passes (`availability_service` applies expiry
on read), and a coupon enters and leaves its `valid_from`/`valid_until` window.
`run_forever` looks every `SWEEP_SECONDS` for any such moment that has just
passed and purges as if it had been written. The Redis caches added for the
storefront go further and never outlive the next such moment
(`seconds_to_next_*`), so the API itself is exactly as fresh as before.

**The purge.** Redis keys first — once straight after the commit, and again
`REDELETE_DELAY` later, because a read that began before the commit can write
the old answer back after the first delete — then, once that second delete is
done, one POST of the web tags to `STOREFRONT_REVALIDATE_URL`, where the
storefront calls `revalidateTag`. Commits within that delay coalesce into one
call. Never raises, never blocks a request: it runs as tracked tasks after the
commit (CLAUDE.md #5). Without the URL and secret the Redis half still runs and
the web half is skipped — and the storefront, which only lengthens its TTL when
it has the same secret, stays on its one-minute expiry.

What this does not see, by design: raw `text()` SQL and a migration. The
deploy flushes every Redis prefix after migrating, and the storefront's TTL —
ten minutes with the purge, one without — is the bound for the rest.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import event, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core import advisory_lock, heartbeat
from app.core.background import spawn_tracked
from app.core.cache import cache_delete_pattern
from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "REDIS_PATTERNS",
    "TABLE_TAGS",
    "WEB_TAGS",
    "crossed_boundaries",
    "enabled",
    "flush_pending",
    "purge",
    "run_forever",
    "seconds_to_next_catalogue_boundary",
    "seconds_to_next_promo_boundary",
]

CATALOGUE = "catalogue"
PROMO = "promo"
CMS = "cms"
BLOG = "blog"
I18N = "i18n"
REDIRECTS = "redirects"

#: Which storefront caches a committed write to each table can have changed.
#: The catalogue set is everything a product page, a listing or the category
#: list is derived from — including what each branch has in stock, which
#: branches the website takes its union over (active, online, not busy, on the
#: active delivery map) and the map itself.
TABLE_TAGS: dict[str, frozenset[str]] = {
    **{
        table: frozenset({CATALOGUE})
        for table in (
            "products",
            "categories",
            "modifiers",
            "modifier_options",
            "product_modifiers",
            "branch_products",
            "branch_modifier_options",
            "branches",
            "delivery_polygon_versions",
            "delivery_polygons",
            "polygon_branch_fulfilment",
        )
    },
    "promo_codes": frozenset({PROMO}),
    "cms_pages": frozenset({CMS}),
    "blog_posts": frozenset({BLOG}),
    "ui_translations": frozenset({I18N}),
    "languages": frozenset({I18N}),
    "url_redirects": frozenset({REDIRECTS}),
}

#: The tags the storefront's fetches carry (`apps/web/lib/cache-policy.ts`
#: `CACHE_TAGS`). `redirects` is not one: the proxy holds its own per-instance
#: table for a minute and no data-cache entry carries it.
WEB_TAGS: frozenset[str] = frozenset({CATALOGUE, PROMO, CMS, BLOG, I18N})

#: The Redis keys behind each tag — every read-through prefix that answers from
#: those tables. Catalogue mirrors `catalogue_cache._PATTERNS`, which a test
#: holds it to.
REDIS_PATTERNS: dict[str, tuple[str, ...]] = {
    CATALOGUE: (
        "products:featured:*",
        "products:cart_addons:*",
        "products:page:*",
        "categories:channel:*",
    ),
    PROMO: ("promo:featured",),
    CMS: ("cms:*",),
    BLOG: ("blog:*",),
    I18N: ("i18n:*",),
    REDIRECTS: ("redirects:*",),
}

#: How long after a commit the Redis keys are deleted a second time, and the web
#: purged. Long enough for a read that started before the commit to have
#: written its answer back; short enough to be invisible to an admin.
REDELETE_DELAY = 1.0

#: How often time-driven changes (a stockout's expiry, a coupon's window) are
#: looked for. Half the storefront's old TTL, so a lapse reaches the page no
#: later than it used to.
SWEEP_SECONDS = 30

#: Per-call budget and retries for the POST to the storefront.
_HTTP_TIMEOUT = 5.0
_RETRY_DELAYS = (1.0, 4.0)

_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4817

_SESSION_KEY = "mm.storefront_tags"


def enabled() -> bool:
    """Whether the web half of the purge is configured — both halves or neither."""
    return bool(
        settings.STOREFRONT_REVALIDATE_URL.strip()
        and settings.STOREFRONT_REVALIDATE_SECRET.strip()
    )


# ─── Seeing writes ────────────────────────────────────────────────────────────


def _tags_for_table(table: Any) -> frozenset[str]:
    return TABLE_TAGS.get(getattr(table, "name", None) or "", frozenset())


def _mark(session: Session, tags: Iterable[str]) -> None:
    session.info.setdefault(_SESSION_KEY, set()).update(tags)


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _flush_context: Any) -> None:
    # `new`/`dirty`/`deleted` still hold the pre-flush state here. `dirty` also
    # lists objects whose attributes were set to what they already were, so it
    # is narrowed to real changes.
    tags: set[str] = set()
    for obj in (*session.new, *session.deleted):
        tags |= _tags_for_table(getattr(type(obj), "__table__", None))
    for obj in session.dirty:
        found = _tags_for_table(getattr(type(obj), "__table__", None))
        if found and not found <= tags and session.is_modified(obj):
            tags |= found
    if tags:
        _mark(session, tags)


@event.listens_for(Session, "do_orm_execute")
def _do_orm_execute(state: Any) -> Any:
    # Bulk statements through the session never pass through a flush.
    if not (state.is_update or state.is_delete or state.is_insert):
        return None
    tags = _tags_for_table(getattr(state.statement, "table", None))
    if not tags:
        return None
    if state.is_insert:
        _mark(state.session, tags)
        return None
    # Run it here to see whether it touched anything. `invoke_statement` runs
    # the remaining `do_orm_execute` handlers too (`counter_bundle_cache`'s), so
    # nothing downstream is skipped; the result goes back to the caller as-is.
    result = state.invoke_statement()
    if getattr(result, "rowcount", -1) != 0:
        _mark(state.session, tags)
    return result


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    # A released savepoint is not a commit anyone else can see yet.
    if session.in_nested_transaction():
        return
    tags = session.info.pop(_SESSION_KEY, None)
    if tags:
        purge(tags)


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: Any) -> None:
    # The outermost transaction ended without the commit above taking the tags:
    # it rolled back, so nothing it wrote is anybody's to purge.
    if transaction.parent is None:
        session.info.pop(_SESSION_KEY, None)


# ─── The purge ────────────────────────────────────────────────────────────────

_pending: set[str] = set()
_flusher: asyncio.Task | None = None


def purge(tags: Iterable[str]) -> None:
    """Retire every cache behind *tags*, now and after `REDELETE_DELAY`.

    Synchronous so a session event can call it. Without a running event loop (a
    sync script) there is no way to run network work, and the TTL is the bound.
    """
    tags = set(tags)
    if not tags:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info("No event loop; storefront purge of %s skipped", sorted(tags))
        return
    spawn_tracked(_delete_redis(tags), name="storefront-cache:redis")
    global _flusher
    _pending.update(tags)
    # One flusher at a time gathers every commit inside its delay. A flusher
    # left on a loop that has since closed (a test's) never finishes, so it
    # does not count.
    if _flusher is None or _flusher.done() or _flusher.get_loop() is not loop:
        _flusher = spawn_tracked(_flush_after_delay(), name="storefront-cache:purge")


async def _flush_after_delay() -> None:
    await asyncio.sleep(REDELETE_DELAY)
    await flush_pending()


async def flush_pending() -> None:
    """Second Redis delete, then the web purge, for everything queued so far."""
    while _pending:
        tags = set(_pending)
        _pending.clear()
        await _delete_redis(tags)
        web = sorted(tags & WEB_TAGS)
        if web:
            await _revalidate_web(web)


async def _delete_redis(tags: Iterable[str]) -> None:
    for tag in sorted(tags):
        for pattern in REDIS_PATTERNS.get(tag, ()):
            await cache_delete_pattern(pattern)


async def _revalidate_web(tags: list[str]) -> bool:
    """POST the tags to the storefront. True when it accepted them; never raises."""
    if not enabled():
        return False
    url = settings.STOREFRONT_REVALIDATE_URL.strip()
    headers = {
        "Authorization": f"Bearer {settings.STOREFRONT_REVALIDATE_SECRET.strip()}"
    }
    for attempt, delay in enumerate((0.0, *_RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, json={"tags": tags}, headers=headers)
            if response.status_code < 300:
                logger.info("storefront purge ok tags=%s", ",".join(tags))
                return True
            # A 4xx is a configuration fault (wrong secret, wrong URL) that a
            # retry cannot fix.
            if response.status_code < 500:
                logger.error(
                    "storefront purge refused (%s) tags=%s",
                    response.status_code,
                    ",".join(tags),
                )
                return False
            logger.warning(
                "storefront purge attempt %s got %s", attempt + 1, response.status_code
            )
        except Exception as exc:  # noqa: BLE001 — a purge must never raise
            logger.warning("storefront purge attempt %s failed: %s", attempt + 1, exc)
    logger.error(
        "storefront purge gave up tags=%s; the storefront TTL is the bound",
        ",".join(tags),
    )
    return False


# ─── Seeing time pass ─────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def crossed_boundaries(
    db: AsyncSession, since: datetime, until: datetime
) -> set[str]:
    """Tags whose content changed by the clock alone in `(since, until]`.

    A stockout is over once `out_of_stock_until <= now` (`availability_service`),
    so its moment counts when it lands in `(since, until]`. A coupon is live
    while `valid_from <= now` and `valid_until >= now`
    (`promo_code_service.advertisable`) — it arrives at `valid_from` and is gone
    just after `valid_until`, hence the half-open window the other way round
    for the second.
    """
    from app.models.menu import BranchModifierOption, BranchProduct
    from app.models.promo_code import PromoCode

    tags: set[str] = set()
    for model in (BranchProduct, BranchModifierOption):
        lapsed = await db.scalar(
            select(model.out_of_stock_until)
            .where(
                model.out_of_stock_until > since,
                model.out_of_stock_until <= until,
            )
            .limit(1)
        )
        if lapsed is not None:
            tags.add(CATALOGUE)
            break
    moved = await db.scalar(
        select(PromoCode.id)
        .where(
            or_(
                (PromoCode.valid_from > since) & (PromoCode.valid_from <= until),
                (PromoCode.valid_until >= since) & (PromoCode.valid_until < until),
            )
        )
        .limit(1)
    )
    if moved is not None:
        tags.add(PROMO)
    return tags


def _seconds_until(moment: datetime | None, now: datetime, cap: int) -> int:
    if not isinstance(moment, datetime):
        return cap
    # +1 so the entry is gone by the time the moment has passed, not just before.
    return max(1, min(cap, math.ceil((moment - now).total_seconds()) + 1))


async def seconds_to_next_catalogue_boundary(db: Any, cap: int) -> int:
    """How long a catalogue answer stays true by the clock: until the next
    stockout lapses, at most *cap*. A Redis TTL, so a cached answer never
    outlives a moment the uncached query would already have seen."""
    from app.models.menu import BranchModifierOption, BranchProduct

    now = _now()
    moments = union_all(
        *(
            select(model.out_of_stock_until.label("at")).where(
                model.out_of_stock_until > now
            )
            for model in (BranchProduct, BranchModifierOption)
        )
    ).subquery()
    nxt = await db.scalar(select(func.min(moments.c.at)))
    return _seconds_until(nxt, now, cap)


async def seconds_to_next_promo_boundary(db: Any, cap: int) -> int:
    """`seconds_to_next_catalogue_boundary` for coupons: until the next code
    opens or closes its window."""
    from app.models.promo_code import PromoCode

    now = _now()
    moments = union_all(
        select(PromoCode.valid_from.label("at")).where(PromoCode.valid_from > now),
        # Live through `valid_until` itself, gone just after it.
        select((PromoCode.valid_until + timedelta(microseconds=1)).label("at")).where(
            PromoCode.valid_until >= now
        ),
    ).subquery()
    nxt = await db.scalar(select(func.min(moments.c.at)))
    return _seconds_until(nxt, now, cap)


async def run_forever() -> None:
    """Purge what the clock changed, every `SWEEP_SECONDS`, leader-elected.

    The window starts one sweep back from boot so a restart does not skip the
    moments that passed while it was down for less than that; anything older
    was already bounded by the storefront's TTL.
    """
    logger.info("Storefront boundary sweep started (every %ss)", SWEEP_SECONDS)
    since = _now() - timedelta(seconds=SWEEP_SECONDS)
    while True:
        try:
            await asyncio.sleep(SWEEP_SECONDS)
            await heartbeat.beat("storefront_boundaries")
            until = _now()
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="storefront boundary sweep"
            ) as db:
                if db is None:
                    # Another slot is sweeping; keep the window moving with it.
                    since = until
                    continue
                tags = await crossed_boundaries(db, since, until)
                await db.commit()
            since = until
            if tags:
                logger.info("Storefront boundary passed: purging %s", sorted(tags))
                purge(tags)
        except asyncio.CancelledError:
            logger.info("Storefront boundary sweep stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Storefront boundary sweep tick failed")
