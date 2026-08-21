"""
The redirect table's invariants, against a real database.

Three of the four ways a redirect table goes wrong are write-time problems, and
every one of them is about the interaction between rows rather than any single
row — a loop needs two, a chain needs two, a destination that is itself a source
needs two. None of that can be seen from a mocked session that answers "no rows"
to everything, so this module skips itself when there is no database to talk to.

The fourth way is a row nobody dares delete because nobody knows whether it
still fires, which is what `hit_count` is for and is tested at the end.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import ConflictError
from app.models.url_redirect import UrlRedirect
from app.schemas.redirect import RedirectCreate, RedirectUpdate
from app.services import redirect_service as svc

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)

#: Every path these tests invent starts here, so cleanup can find them all and
#: nothing collides with the rows migration 120 seeded.
PREFIX = "/pytest-"


@pytest.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        yield db
        await db.rollback()
        await db.execute(
            delete(UrlRedirect).where(UrlRedirect.from_path.like(f"{PREFIX}%"))
        )
        await db.execute(
            delete(UrlRedirect).where(UrlRedirect.to_path.like(f"{PREFIX}%"))
        )
        await db.commit()
    await engine.dispose()


async def rows(db) -> dict[str, UrlRedirect]:
    result = await db.execute(
        select(UrlRedirect).where(UrlRedirect.from_path.like(f"{PREFIX}%"))
    )
    return {r.from_path: r for r in result.scalars()}


async def add(db, frm: str, to: str, **kw):
    return await svc.create(db, RedirectCreate(from_path=frm, to_path=to, **kw))


# ── Chains ───────────────────────────────────────────────────────────────────


async def test_a_chain_is_collapsed_on_write(session):
    """
    A→B, then B→C, must leave A→C — not A→B→C.

    A chain is not wrong, it is expensive: two round trips for the visitor and a
    little ranking signal lost at each hop. And it is one more rename away from
    being a loop, which is not expensive but broken.
    """
    await add(session, f"{PREFIX}a", f"{PREFIX}b")
    await add(session, f"{PREFIX}b", f"{PREFIX}c")
    await session.flush()

    live = await rows(session)
    assert live[f"{PREFIX}a"].to_path == f"{PREFIX}c"
    assert live[f"{PREFIX}b"].to_path == f"{PREFIX}c"


async def test_a_destination_may_not_itself_redirect_away(session):
    """
    Writing B→C while A→B exists is the collapse above. Writing A→B while B→C
    exists is the other order, and the answer is that B stops redirecting: it is
    where A now lands, so it has to be somewhere you can arrive.
    """
    await add(session, f"{PREFIX}b", f"{PREFIX}c")
    await add(session, f"{PREFIX}a", f"{PREFIX}b")
    await session.flush()

    live = await rows(session)
    assert live[f"{PREFIX}a"].to_path == f"{PREFIX}b"
    # Deactivated rather than deleted: the row is a record that this URL once
    # moved, and it should read as struck through rather than vanish.
    assert live[f"{PREFIX}b"].is_active is False


async def test_a_rule_may_not_point_at_itself(session):
    with pytest.raises(ConflictError):
        await add(session, f"{PREFIX}a", f"{PREFIX}a")


async def test_one_destination_per_source(session):
    await add(session, f"{PREFIX}a", f"{PREFIX}b")
    with pytest.raises(ConflictError) as err:
        await add(session, f"{PREFIX}a", f"{PREFIX}z")
    # The message names where it already goes, so the operator can decide
    # whether they meant to edit that rule.
    assert f"{PREFIX}b" in str(err.value)


async def test_editing_a_rule_collapses_around_its_new_source(session):
    """
    An edit runs the same invariants as a create, which matters most when the
    edit moves the rule's *source* onto a path something else already points at.

    `p→q` and `r→s` are unrelated until `r` is renamed to `q`; at that moment
    `p→q→s` exists and has to become `p→s`.
    """
    await add(session, f"{PREFIX}p", f"{PREFIX}q")
    created = await add(session, f"{PREFIX}r", f"{PREFIX}s")

    await svc.update_one(session, created.id, RedirectUpdate(from_path=f"{PREFIX}q"))
    await session.flush()

    live = await rows(session)
    assert live[f"{PREFIX}q"].to_path == f"{PREFIX}s"
    assert live[f"{PREFIX}p"].to_path == f"{PREFIX}s"


async def test_a_collapsed_rule_does_not_follow_a_later_edit(session):
    """
    The other side of collapsing, asserted so nobody 'fixes' it.

    Once `a→b` and `b→x` have been collapsed to `a→x`, the link between `a` and
    `b` is gone — that is the point. Editing the `b` rule afterwards moves `b`
    and leaves `a` where it was pointing, because `a` no longer has an opinion
    about `b`. Both rules are still exactly one hop, which is the invariant that
    matters.
    """
    await add(session, f"{PREFIX}a", f"{PREFIX}b")
    created = await add(session, f"{PREFIX}b", f"{PREFIX}x")
    await svc.update_one(session, created.id, RedirectUpdate(to_path=f"{PREFIX}c"))
    await session.flush()

    live = await rows(session)
    assert live[f"{PREFIX}b"].to_path == f"{PREFIX}c"
    assert live[f"{PREFIX}a"].to_path == f"{PREFIX}x"


# ── Renames ──────────────────────────────────────────────────────────────────


async def test_a_rename_writes_a_prefix_rule(session):
    """
    Prefix, because the category page is the smaller half of what moved. There
    are 36 product URLs nested under the eight category slugs, all indexed, and
    an exact-match rule would leave every one of them behind.
    """
    await svc.record_rename(session, "pytest-old", "pytest-new")
    await session.flush()

    row = (
        await session.execute(
            select(UrlRedirect).where(UrlRedirect.from_path == "/pytest-old")
        )
    ).scalar_one()
    assert row.to_path == "/pytest-new"
    assert row.is_prefix is True
    assert row.source == "category_rename"


async def test_renaming_twice_does_not_leave_a_dead_end(session):
    """A→B then B→C, arrived at through two console renames rather than two
    hand-written rules. The first rename's rule has to follow the second."""
    await svc.record_rename(session, "pytest-a", "pytest-b")
    await svc.record_rename(session, "pytest-b", "pytest-c")
    await session.flush()

    live = await rows(session)
    assert live["/pytest-a"].to_path == "/pytest-c"


async def test_renaming_back_does_not_make_a_loop(session):
    """
    The one that would actually happen: somebody renames a category, does not
    like it, and renames it back within the minute. Naively that is A→B and B→A,
    and a browser bouncing between them until it gives up.
    """
    await svc.record_rename(session, "pytest-a", "pytest-b")
    await svc.record_rename(session, "pytest-b", "pytest-a")
    await session.flush()

    live = await rows(session)
    active = {p: r.to_path for p, r in live.items() if r.is_active}
    # Whatever survives, nothing may send a visitor back where they came from.
    for frm, to in active.items():
        assert active.get(to) != frm, f"{frm} and {to} redirect to each other"


async def test_a_rename_that_changes_nothing_writes_nothing(session):
    await svc.record_rename(session, "pytest-same", "pytest-same")
    await session.flush()
    assert await rows(session) == {}


# ── Hits ─────────────────────────────────────────────────────────────────────


async def test_a_hit_is_counted_and_stamped(session):
    await add(session, f"{PREFIX}a", f"{PREFIX}b")
    await session.flush()

    await svc.apply_hit(session, f"{PREFIX}a")
    await svc.apply_hit(session, f"{PREFIX}a")
    await session.flush()
    session.expire_all()

    row = (
        await session.execute(
            select(UrlRedirect).where(UrlRedirect.from_path == f"{PREFIX}a")
        )
    ).scalar_one()
    assert row.hit_count == 2
    assert row.last_hit_at is not None


async def test_a_hit_on_a_path_with_no_rule_is_harmless(session):
    """
    The storefront reports the rule it matched, so this should not happen — but
    it reports after the redirect has already gone out, and nothing at that
    point is allowed to raise.
    """
    await svc.apply_hit(session, f"{PREFIX}not-a-rule")
    assert await rows(session) == {}


async def test_record_hit_never_raises(session):
    """
    The wrapper's whole job.

    Deliberately given a session factory that cannot work — which is also what
    the full test suite hands it, since the module-level engine is bound to
    whichever event loop reached it first. That this test passes *because* the
    write failed is the point: the storefront calls this after the redirect has
    gone out, and an exception there has nothing left to protect.
    """
    import app.services.redirect_service as module

    class Exploding:
        def __call__(self):
            raise RuntimeError("no database today")

    original = module.AsyncSessionFactory
    module.AsyncSessionFactory = Exploding()
    try:
        await svc.record_hit(f"{PREFIX}a")
    finally:
        module.AsyncSessionFactory = original
