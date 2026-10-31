"""
A process-local cache for the one-row `business_settings` table.

`business_day_service.resolve_timezone` reads that row to learn the shop's
timezone, and it sits under `current_business_date`, the counter bundle poll,
storefront availability, the dashboard and the daily e-mails — 316k identical
`SELECT … FROM business_settings LIMIT 1` in six weeks of production for a row
that changes when an owner edits the console. This keeps a detached copy of
that row's columns and reuses it while three things hold, the same contract
`app/services/pos/counter_bundle_cache.py` keeps for the counter bundle:

1. **The generation has not moved.** A committed ORM write to a table in
   `WATCHED_TABLES` — a flush of a changed instance or a bulk
   `update()/insert()/delete()` through the session — bumps this process's
   generation at once, and a shared Redis counter shortly after for the other
   processes (storefront API, register API, scheduler, blue and green), which
   look at it at most once a `SHARED_PROBE_INTERVAL`. Seen by SQLAlchemy
   session events, so the console route, a seed and a script are all covered
   without knowing this module exists.
2. **The entry is younger than `TTL_CEILING`.** Raw SQL, a direct prod fix, a
   migration and a Redis outage are not seen by (1); a minute is the most any
   of them can leave a process on the old row.
3. **The caller's own transaction has not written the table.** A session that
   has flushed (or bulk-executed) a write to a watched table reads the row from
   the database for the rest of that transaction, so a request that changes
   the timezone and then reads it sees its own write exactly as before. The
   read autoflushes first, as the query it replaces did, so an edit still
   pending in the session counts too.

Purely an optimisation: what comes back is a read-only mapping of plain column
values — never an ORM instance, so nothing is shared between sessions — and a
caller that is not a real session (a unit-test double) bypasses the cache and
reads straight through, as before.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.background import spawn_tracked
from app.core.cache import cache_get, cache_incr
from app.models.business_settings import BusinessSettings

logger = logging.getLogger(__name__)

__all__ = [
    "SHARED_PROBE_INTERVAL",
    "TTL_CEILING",
    "WATCHED_TABLES",
    "bump",
    "business_settings_row",
    "clear",
]

#: Tables whose committed ORM writes retire every cached entry.
WATCHED_TABLES: frozenset[str] = frozenset({"business_settings"})

#: The longest an entry is reused, whatever the generation says.
TTL_CEILING = 60.0

#: How often the shared Redis generation is re-read. The bound on how long a
#: write committed by ANOTHER process can go unseen here (this process's own
#: writes are seen at once).
SHARED_PROBE_INTERVAL = 1.0

#: Shared by every process; INCR'd after a commit that touched a watched table.
REDIS_KEY = "refdata:generation"

_DIRTY = "_reference_tables_written"

_local_generation = 0
_shared_generation: int | None = None
_shared_probed_at = float("-inf")

#: key → (generation it was read under, monotonic read time, value)
_entries: dict[str, tuple[tuple[int, int | None], float, Any]] = {}


# ─── The generation ───────────────────────────────────────────────────────────


async def _generation() -> tuple[int, int | None]:
    global _shared_generation, _shared_probed_at
    now = time.monotonic()
    if now - _shared_probed_at >= SHARED_PROBE_INTERVAL:
        shared = await cache_get(REDIS_KEY)
        try:
            _shared_generation = int(shared) if shared is not None else None
        except (TypeError, ValueError):
            _shared_generation = None
        _shared_probed_at = now
    return _local_generation, _shared_generation


def bump() -> None:
    """Retire every entry: here at once, elsewhere via Redis."""
    global _local_generation
    _local_generation += 1
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (a sync script): nothing else to tell
    spawn_tracked(cache_incr(REDIS_KEY), name="refdata-generation")


def clear() -> None:
    """Forget every entry and the last Redis reading (tests)."""
    global _shared_generation, _shared_probed_at
    _entries.clear()
    _shared_generation = None
    _shared_probed_at = float("-inf")


# ─── Which sessions may use it ────────────────────────────────────────────────


#: `get_db_lazy`'s stand-in before it has opened a session: nothing pending,
#: nothing written, and a cache hit keeps it from ever checking one out.
_UNOPENED = object()


def _session_of(db: Any) -> Any:
    """The `AsyncSession` behind `db`, `_UNOPENED`, or None for anything that
    is not a real session (a unit-test double), which reads straight through."""
    if isinstance(db, AsyncSession):
        return db
    opened = getattr(db, "opened", None)
    if isinstance(opened, bool):
        if not opened:
            return _UNOPENED
        inner = getattr(db, "_session", None)
        return inner if isinstance(inner, AsyncSession) else None
    return None


async def _cached(db: Any, key: str, load) -> Any:
    session = _session_of(db)
    if session is None:
        return await load(db)
    if session is not _UNOPENED:
        # The query this replaces autoflushed first. Do the same — honouring
        # `no_autoflush` — so pending changes reach the database at the same
        # moment as before, and a pending edit to a watched table marks the
        # session (via `after_flush`) and is read back below, not served stale.
        sync = session.sync_session
        pending = (*sync.new, *sync.dirty, *sync.deleted)
        if pending and sync.autoflush:
            await session.flush()
            pending = ()
        # Under `no_autoflush` an edit to a watched row stays pending, and the
        # old query handed back the session's own (edited) instance — so read
        # through, which does the same.
        if session.info.get(_DIRTY) or any(
            _table_name(obj) in WATCHED_TABLES for obj in pending
        ):
            return await load(db)
    current = await _generation()
    entry = _entries.get(key)
    if (
        entry is not None
        and entry[0] == current
        and time.monotonic() - entry[1] < TTL_CEILING
    ):
        return entry[2]
    # The generation is read BEFORE the load: a write committed while the row
    # is in flight moves the generation past the one stored here, so the next
    # read reloads instead of serving what this load saw.
    value = await load(db)
    _entries[key] = (current, time.monotonic(), value)
    return value


# ─── The cached reads ─────────────────────────────────────────────────────────


async def _load_business_settings(db: Any) -> Mapping[str, Any] | None:
    # The same statement `resolve_timezone` always ran; only its column values
    # leave this function.
    row = (await db.execute(select(BusinessSettings).limit(1))).scalar_one_or_none()
    if not isinstance(row, BusinessSettings):
        return None
    return MappingProxyType(
        {
            attr.key: copy.deepcopy(getattr(row, attr.key))
            for attr in BusinessSettings.__mapper__.column_attrs
        }
    )


async def business_settings_row(db: Any) -> Mapping[str, Any] | None:
    """The `business_settings` row's column values (read-only), or None when
    the table is empty."""
    return await _cached(db, "business_settings", _load_business_settings)


# ─── Seeing writes ────────────────────────────────────────────────────────────


def _table_name(obj: Any) -> str | None:
    table = getattr(type(obj), "__table__", None)
    return getattr(table, "name", None)


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _flush_context: Any) -> None:
    # `new`/`dirty`/`deleted` still hold the pre-flush state here.
    if session.info.get(_DIRTY):
        return
    for obj in (*session.new, *session.dirty, *session.deleted):
        if _table_name(obj) in WATCHED_TABLES:
            session.info[_DIRTY] = True
            return


@event.listens_for(Session, "do_orm_execute")
def _do_orm_execute(state: Any) -> None:
    # Bulk `update(BusinessSettings)` / `insert(...)` / `delete(...)` through
    # the session never pass through a flush.
    if not (state.is_update or state.is_delete or state.is_insert):
        return
    table = getattr(state.statement, "table", None)
    if getattr(table, "name", None) in WATCHED_TABLES:
        state.session.info[_DIRTY] = True


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    # Fires for a savepoint release too; only the outermost commit makes the
    # write visible to anyone else, and bumping earlier would let a concurrent
    # read cache the old row under the new generation.
    if session.in_nested_transaction():
        return
    if session.info.pop(_DIRTY, False):
        bump()
    # Deliberately no `after_rollback` counterpart: that event fires for a
    # savepoint rollback too, and clearing the flag there would forget a write
    # the outer transaction still holds. A rolled-back write only costs one
    # spurious reload at the session's next commit.
