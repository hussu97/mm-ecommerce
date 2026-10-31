"""
The counter bundle's build cache: build once per change, not once per poll.

Every register polls `GET /pos/counter/bundle` about once a minute, and nine
polls in ten come back 304. Building the hashed body takes ~20 queries (a
recursive menu-tree CTE, promotions with their usage, tax groups, ~140 products
through Pydantic) — so before this module the register's busiest endpoint spent
most of its CPU proving that nothing had changed. Now the built body and its
hash are kept per branch, in this process, and reused while three things hold:

1. **The generation has not moved.** Every committed ORM write that touches a
   table the body is built from (`BUNDLE_INPUT_TABLES`) bumps a generation:
   this process's counter at once, and a Redis counter shortly after, so the
   other processes (storefront API, register API, scheduler, blue and green)
   see it on their next poll. The write is detected by SQLAlchemy session
   events, not by a call every router has to remember — so a console page,
   a bulk import, a script on the ORM and a sweep are all covered without
   knowing this module exists. Adding a table to `build_body` means adding it
   to `BUNDLE_INPUT_TABLES`; a test diffs the two.
2. **The entry is younger than `TTL_CEILING`.** Raw SQL (`text()`), a direct
   prod fix, a migration and a Redis outage are not seen by (1). Five minutes
   is the bound on how long any of them can leave a register on the old
   inputs — and stale is never wrong money: ingest re-prices every synced sale
   with the bundle it cites and flags a difference.
3. **The usage-limited promotions it judged are still judged the same.** The
   one input that moves with sales rather than with edits: a promotion whose
   usage limit is used up drops out of the bundle (and comes back if an order
   carrying it is voided). The entry remembers the limits it read; a reuse
   re-counts their usage in one query and rebuilds if the exhausted set moved.

Purely an optimisation: an entry that fails any check is rebuilt from the
database exactly as before, and nothing here is consulted by ingest, which
reads the stored `pos_config_bundles` row.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.background import spawn_tracked
from app.core.cache import cache_get, cache_incr

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLE_INPUT_TABLES",
    "TTL_CEILING",
    "Entry",
    "bump",
    "clear",
    "generation",
    "lookup",
    "store",
]

#: Every table `counter_bundle_service.build_body` reads. A committed ORM write
#: to any of them retires every branch's cached body.
BUNDLE_INPUT_TABLES: frozenset[str] = frozenset(
    {
        "branch_channel_tax_configs",
        "branches",
        "business_settings",
        "kitchen_flow_categories",
        "kitchen_flows",
        "legal_entities",
        "menu_group_products",
        "menu_groups",
        "modifier_options",
        "modifiers",
        "payment_methods",
        "product_modifiers",
        "products",
        "promotions",
        "reasons",
        "tax_group_taxes",
        "tax_groups",
        "taxes",
    }
)

#: The longest a cached body is reused, whatever the generation says.
TTL_CEILING = 300.0

#: Shared by every process; INCR'd after a commit that touched an input.
REDIS_KEY = "pos:counter_bundle:generation"

_DIRTY = "_counter_bundle_inputs_written"

_local_generation = 0


@dataclass
class Entry:
    """One branch's built body, and what it was built under."""

    hash: str
    payload: dict[str, Any]
    generation: tuple[int, int | None]
    built_at: float
    #: The usage limit of every counter promotion that has one, by id …
    usage_caps: dict[uuid.UUID, int] = field(default_factory=dict)
    #: … and which of them were used up (left out of the body).
    exhausted: frozenset[uuid.UUID] = frozenset()
    #: Whether the `pos_config_bundles` row for `hash` is known to be committed.
    persisted: bool = False


_entries: dict[uuid.UUID, Entry] = {}


# ─── The generation ───────────────────────────────────────────────────────────


async def generation() -> tuple[int, int | None]:
    """(this process's bumps, the shared Redis counter — None without Redis)."""
    shared = await cache_get(REDIS_KEY)
    try:
        return _local_generation, int(shared) if shared is not None else None
    except (TypeError, ValueError):
        return _local_generation, None


def bump() -> None:
    """Retire every cached body: here at once, elsewhere via Redis."""
    global _local_generation
    _local_generation += 1
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (a sync script): nothing else to tell
    spawn_tracked(cache_incr(REDIS_KEY), name="counter-bundle-generation")


# ─── The cache ────────────────────────────────────────────────────────────────


def lookup(branch_id: uuid.UUID, current: tuple[int, int | None]) -> Entry | None:
    """The branch's entry if it was built under `current` and is in date."""
    entry = _entries.get(branch_id)
    if entry is None:
        return None
    if entry.generation != current:
        return None
    if time.monotonic() - entry.built_at >= TTL_CEILING:
        return None
    return entry


def store(branch_id: uuid.UUID, entry: Entry) -> None:
    _entries[branch_id] = entry


def clear() -> None:
    """Forget every entry (tests)."""
    _entries.clear()


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
        if _table_name(obj) in BUNDLE_INPUT_TABLES:
            session.info[_DIRTY] = True
            return


@event.listens_for(Session, "do_orm_execute")
def _do_orm_execute(state: Any) -> None:
    # Bulk `update(Product)` / `insert(...)` / `Table.delete()` through the
    # session never pass through a flush.
    if not (state.is_update or state.is_delete or state.is_insert):
        return
    table = getattr(state.statement, "table", None)
    if getattr(table, "name", None) in BUNDLE_INPUT_TABLES:
        state.session.info[_DIRTY] = True


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    # Fires for a savepoint release too; only the outermost commit makes the
    # write visible to anyone else, and bumping earlier would let a concurrent
    # build cache the old rows under the new generation.
    if session.in_nested_transaction():
        return
    if session.info.pop(_DIRTY, False):
        bump()
