"""Read one integrator's live menu / hours into the channel-neutral shape.

The read side reuses the existing session/browser plumbing rather than reinventing
it: a marketplace reader loads the encrypted `aggregator_session` and replays it
the way the ingest providers do (`aggregator_base.request_json` — TLS-impersonated,
cookie-jar); the Foodics reader uses the console session the `foodics_provider`
already logs in with, and reads the **`Grubtech` group** (membership) + **price
tag** (the aggregator price) that the audit identified as the real integrated-branch
menu. Hours come from each portal's own schedule editor.

**Phase 1 status.** The dispatch, the normalized contract and the source mapping are
in place; the per-portal fetchers are **gated stubs** — `refresh_target` only calls
them when `CATALOG_SYNC_READ_ENABLED` is on, and until each is implemented it raises
`AggregatorUnavailableError`, which the sweep records as a snapshot `error` without
crashing. The drift pipeline runs off whatever snapshots exist, so it is fully
exercisable (see the tests) before a single live reader ships. Implement one reader
at a time here, newest-value-first (Foodics `Grubtech`, then the self-service
channels Keeta/Careem, then the anti-bot ones), each behind the same flag.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog_sync import (
    SOURCE_BROWSER,
    SOURCE_FOODICS_API,
    SOURCE_HTTP,
    TARGET_FOODICS,
)
from app.services.aggregators.menu_normalized import NormalizedHours, NormalizedMenu
from app.services.providers.aggregator_base import AggregatorUnavailableError

#: How each target's read is obtained, stamped onto the snapshot. The
#: TLS-impersonated channels answer over http; noon/talabat menu pages may need the
#: headed browser; Foodics answers on its console API.
_SOURCE_BY_TARGET: dict[str, str] = {
    "careem": SOURCE_HTTP,
    "keeta": SOURCE_HTTP,
    "deliveroo": SOURCE_BROWSER,
    "talabat": SOURCE_BROWSER,
    "noon": SOURCE_BROWSER,
    TARGET_FOODICS: SOURCE_FOODICS_API,
}


def source_for(target: str) -> str:
    return _SOURCE_BY_TARGET.get(target, SOURCE_HTTP)


async def fetch_menu(
    db: AsyncSession, *, target: str, branch_id: Any
) -> NormalizedMenu:
    """Read one outlet's live menu. Raises until the per-target reader ships."""
    reader = _MENU_READERS.get(target)
    if reader is None:
        raise AggregatorUnavailableError(
            f"live menu reader for {target!r} is not implemented yet "
            "(Phase 1 drift runs off stored snapshots)"
        )
    return await reader(db, branch_id)


async def fetch_hours(
    db: AsyncSession, *, target: str, branch_id: Any
) -> NormalizedHours:
    """Read one outlet's live hours. Raises until the per-target reader ships."""
    reader = _HOURS_READERS.get(target)
    if reader is None:
        raise AggregatorUnavailableError(
            f"live hours reader for {target!r} is not implemented yet "
            "(Phase 1 drift runs off stored snapshots)"
        )
    return await reader(db, branch_id)


# ── Reader registries ─────────────────────────────────────────────────────────
# Empty in Phase 1 — every target falls through to the "not implemented yet"
# raise above, which the sweep records without crashing. A new reader is a single
# entry here plus its `async def _read_<target>_menu(db, branch_id)` function; the
# Foodics one reads the `Grubtech` group + price tag, the marketplace ones replay
# the stored session through `aggregator_base`.
_MENU_READERS: dict[str, Any] = {}
_HOURS_READERS: dict[str, Any] = {}
