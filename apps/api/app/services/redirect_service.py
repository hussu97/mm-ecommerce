"""
The URL redirect table, and the rules that keep it flat.

Every write goes through here rather than through the model, because three of
the four ways a redirect table goes wrong are write-time problems and only one
of them is visible to the person making the write:

* **Loops.** A→B and later B→A leaves a browser bouncing until it gives up.
* **Chains.** A→B and later B→C is not wrong, but it costs a second round trip
  per visit and bleeds a little ranking signal at every hop. Collapsed on write
  into A→C, so the table only ever describes one jump.
* **A destination that is itself a source.** Renaming `brownies` back after
  `cat-brownies` → `brownies` would otherwise leave `brownies` → something else
  while the new slug *is* `brownies`.

The fourth is a row nobody dares delete because nobody knows if it still fires,
which is what `hit_count` and `last_hit_at` are for.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionFactory
from app.core.exceptions import ConflictError, NotFoundError
from app.models.url_redirect import UrlRedirect
from app.schemas.redirect import (
    RedirectCreate,
    RedirectResponse,
    RedirectRule,
    RedirectUpdate,
)

logger = logging.getLogger(__name__)

#: How far the resolver will walk before deciding it is in a loop.
#:
#: The table is collapsed on write, so a correctly built one never needs more
#: than a single hop. This exists for a table that was not built by this module
#: — a hand-written SQL fix, a restored dump, a row inserted before the collapse
#: rule existed — where the alternative to a cap is a request that never ends.
MAX_HOPS = 3


# ── Reads ────────────────────────────────────────────────────────────────────


async def active_rules(db: AsyncSession) -> list[RedirectRule]:
    """
    Every live rule, longest `from_path` first.

    The order is load-bearing for prefix rules: `/cat-cookiemelt` and
    `/cat-cookies` both prefix-match nothing of each other, but `/shop` and
    `/shop/brownies` would, and the more specific rule has to win. Sorting once
    here means the middleware can take the first match instead of scoring them.
    """
    rows = (
        await db.execute(
            select(UrlRedirect)
            .where(UrlRedirect.is_active.is_(True))
            .order_by(func.length(UrlRedirect.from_path).desc(), UrlRedirect.from_path)
        )
    ).scalars()
    return [
        RedirectRule(
            from_path=r.from_path,
            to_path=r.to_path,
            is_prefix=r.is_prefix,
            status_code=r.status_code,
        )
        for r in rows
    ]


async def list_all(db: AsyncSession) -> list[RedirectResponse]:
    """The console's view: inactive rows included, newest first."""
    rows = (
        await db.execute(select(UrlRedirect).order_by(UrlRedirect.created_at.desc()))
    ).scalars()
    return [RedirectResponse.model_validate(r) for r in rows]


# ── The invariants ───────────────────────────────────────────────────────────


async def _collapse_and_clear(
    db: AsyncSession, from_path: str, to_path: str, *, skip_id: UUID | None = None
) -> None:
    """
    Keep the table one hop deep around a rule that is about to exist.

    Two statements, in this order:

    1. Anything already pointing **at** `from_path` is repointed at `to_path`.
       A→B existing while B→C is written leaves A→C, not a walk through B.
    2. Anything whose source **is** `to_path` is deactivated. The destination of
       a live redirect may not itself redirect away — that is a chain the other
       way round, and after a rename-and-rename-back it is a loop.

    Deactivated rather than deleted in step 2: the row is a record of a URL that
    once moved, and an operator looking at the console should see it struck out
    rather than find it silently gone.
    """
    await db.execute(
        update(UrlRedirect)
        .where(
            UrlRedirect.to_path == from_path,
            UrlRedirect.from_path != to_path,
            *((UrlRedirect.id != skip_id,) if skip_id else ()),
        )
        .values(to_path=to_path, updated_at=datetime.now(timezone.utc))
    )
    await db.execute(
        update(UrlRedirect)
        .where(
            UrlRedirect.from_path == to_path,
            *((UrlRedirect.id != skip_id,) if skip_id else ()),
        )
        .values(is_active=False, updated_at=datetime.now(timezone.utc))
    )


def _reject_self(from_path: str, to_path: str) -> None:
    if from_path == to_path:
        raise ConflictError(
            f"'{from_path}' cannot redirect to itself — that is a loop, not a rule."
        )


# ── Writes ───────────────────────────────────────────────────────────────────


async def create(db: AsyncSession, data: RedirectCreate) -> RedirectResponse:
    _reject_self(data.from_path, data.to_path)

    existing = (
        await db.execute(
            select(UrlRedirect).where(UrlRedirect.from_path == data.from_path)
        )
    ).scalar_one_or_none()
    if existing:
        raise ConflictError(
            f"'{data.from_path}' already redirects to '{existing.to_path}'. "
            "Edit that rule rather than adding a second one."
        )

    await _collapse_and_clear(db, data.from_path, data.to_path)

    row = UrlRedirect(**data.model_dump(), source="manual")
    db.add(row)
    await db.flush()
    return RedirectResponse.model_validate(row)


async def update_one(
    db: AsyncSession, redirect_id: UUID, data: RedirectUpdate
) -> RedirectResponse:
    row = (
        await db.execute(select(UrlRedirect).where(UrlRedirect.id == redirect_id))
    ).scalar_one_or_none()
    if not row:
        raise NotFoundError(f"Redirect '{redirect_id}' not found")

    updates = data.model_dump(exclude_unset=True)
    from_path = updates.get("from_path", row.from_path)
    to_path = updates.get("to_path", row.to_path)
    _reject_self(from_path, to_path)

    if from_path != row.from_path:
        clash = (
            await db.execute(
                select(UrlRedirect).where(
                    UrlRedirect.from_path == from_path, UrlRedirect.id != row.id
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise ConflictError(f"'{from_path}' already redirects to '{clash.to_path}'")

    for key, value in updates.items():
        setattr(row, key, value)
    # An operator editing a rename-written rule has taken it over; it should not
    # be silently rewritten the next time that category is renamed.
    if updates:
        row.source = "manual"

    await _collapse_and_clear(db, from_path, to_path, skip_id=row.id)
    await db.flush()
    return RedirectResponse.model_validate(row)


async def delete(db: AsyncSession, redirect_id: UUID) -> None:
    row = (
        await db.execute(select(UrlRedirect).where(UrlRedirect.id == redirect_id))
    ).scalar_one_or_none()
    if not row:
        raise NotFoundError(f"Redirect '{redirect_id}' not found")
    await db.delete(row)
    await db.flush()


async def record_rename(db: AsyncSession, old_slug: str, new_slug: str) -> None:
    """
    A category changed its slug; keep its old URLs working.

    Called from `category_service.update`, in the same transaction, so a rename
    that fails to commit does not leave a redirect claiming it happened.

    A **prefix** rule, because the category page is the smaller half of what
    moved: every product under it — `/cat-mixboxes/mix-cookies-box-of-9` and its
    35 siblings — is an indexed URL that an exact-match rule would not touch.

    Existing rules pointing at the old path are carried forward by
    `_collapse_and_clear`, which is what makes renaming twice safe: the first
    rename's rule follows the second one rather than dead-ending.
    """
    from_path = f"/{old_slug.strip('/').lower()}"
    to_path = f"/{new_slug.strip('/').lower()}"
    if from_path == to_path:
        return

    await _collapse_and_clear(db, from_path, to_path)

    row = (
        await db.execute(select(UrlRedirect).where(UrlRedirect.from_path == from_path))
    ).scalar_one_or_none()
    if row:
        # The slug has been used before and is being retired again. Reuse the
        # row rather than colliding with its unique index — but leave a rule a
        # human edited alone beyond repointing it, since they have taken it over.
        row.to_path = to_path
        row.is_active = True
        row.is_prefix = True
        return

    db.add(
        UrlRedirect(
            from_path=from_path,
            to_path=to_path,
            is_prefix=True,
            status_code=308,
            is_active=True,
            source="category_rename",
            note=f"Category slug renamed from '{old_slug}' to '{new_slug}'.",
        )
    )
    await db.flush()


async def apply_hit(db: AsyncSession, from_path: str) -> None:
    """
    The increment itself, on whichever session is handed to it.

    Split from `record_hit` so the two things it does can be checked separately:
    that the arithmetic is right, and that the wrapper around it cannot throw.
    A path with no rule updates nothing, which is the correct answer rather than
    an error — the storefront reports the rule it matched, so this should not
    happen, but "should not" is not a reason to raise after the fact.
    """
    await db.execute(
        update(UrlRedirect)
        .where(UrlRedirect.from_path == from_path)
        .values(
            hit_count=UrlRedirect.hit_count + 1,
            last_hit_at=datetime.now(timezone.utc),
        )
    )


async def record_hit(from_path: str) -> None:
    """
    Stamp a rule that just fired. Its own session, and it never raises.

    The redirect has already been sent by the time this runs — the storefront
    reports it from `waitUntil`, after the response — so there is nothing left to
    fail, and no request left to fail it. Convention 2: a side-write that must
    not ride the request's transaction and must not be able to take anything
    down, same shape as `webhook_log_service.Recorder.save`.
    """
    try:
        async with AsyncSessionFactory() as db:
            await apply_hit(db, from_path)
            await db.commit()
    except Exception as exc:
        logger.warning("Could not record a redirect hit for %s: %s", from_path, exc)
