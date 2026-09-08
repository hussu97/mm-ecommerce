"""
No scheduler loop may hold a DB session open across an `await` on a third party.

This is the exact failure that took production down twice: a loop opens a
session (checking a connection out of the pool), then `await`s something external
— a courier API, a marketplace pull, Resend — while the session, and its
connection, sit checked out for the whole round-trip (idle-in-transaction, in the
worst case). Enough of those at once and the pool is gone.

The rule is enforced STRUCTURALLY, as a sibling of `test_order_lifecycle_guard`.
Inside the body of a loop/sweep function, an `async with <SessionFactory>()`
block must not contain a DIRECT `await` on a recognised third-party client — an
`httpx`/`resend`/`aiohttp`/`requests` call, or a method on a `provider` / `client`
object. The check is deliberately about the DIRECT shape a new loop is most
likely to grow (open a session, then `await provider....` before closing it); a
call buried inside a service the loop invokes is beyond an AST's structural
reach, so this pairs with code review and the two-engine isolation (a scheduler
session that leaks can only exhaust the small scheduler pool, never a request's).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[2] / "app" / "services"

SESSION_FACTORY_NAMES = {"AsyncSessionFactory", "SchedulerSessionFactory"}

#: Segments of a call's dotted path that mark it as a third-party client.
_THIRD_PARTY_MODULES = {"httpx", "resend", "aiohttp", "requests"}


def _is_loop_body(func: ast.AsyncFunctionDef) -> bool:
    name = func.name
    return (
        name == "run_forever"
        or name.endswith("_forever")
        or name == "_tick"
        or "sweep" in name
    )


def _dotted_segments(call: ast.Call) -> list[str]:
    """The identifier segments of a call target, e.g. `provider.fetch_sales` →
    ['provider', 'fetch_sales']; `httpx.AsyncClient` → ['httpx', 'AsyncClient']."""
    segments: list[str] = []
    node: ast.expr = call.func
    while isinstance(node, ast.Attribute):
        segments.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        segments.append(node.id)
    return list(reversed(segments))


def _is_third_party_call(call: ast.Call) -> bool:
    segments = _dotted_segments(call)
    for seg in segments:
        low = seg.lower()
        if low in _THIRD_PARTY_MODULES:
            return True
        # A receiver that is plainly a provider/client handle.
        if low == "provider" or low.endswith("_provider"):
            return True
        if low == "client" or low.endswith("_client"):
            return True
    return False


def _session_block_third_party_awaits(func: ast.AsyncFunctionDef) -> list[int]:
    """Line numbers of DIRECT third-party awaits held inside a session block."""
    hits: list[int] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.AsyncWith):
            continue
        opens_session = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id in SESSION_FACTORY_NAMES
            for item in node.items
        )
        if not opens_session:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Await)
                and isinstance(inner.value, ast.Call)
                and _is_third_party_call(inner.value)
            ):
                hits.append(inner.lineno)
    return hits


def _offenders_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and _is_loop_body(node):
            for lineno in _session_block_third_party_awaits(node):
                offenders.append(
                    f"{path.relative_to(SERVICES_DIR.parent.parent)}:{lineno} "
                    f"(in {node.name})"
                )
    return offenders


def test_no_loop_holds_a_session_across_a_third_party_await():
    offenders: list[str] = []
    for path in sorted(SERVICES_DIR.rglob("*.py")):
        offenders.extend(_offenders_in(path))
    assert not offenders, (
        "A scheduler loop holds a DB session open across an await on a third "
        "party. Close the session (commit) BEFORE the external call and reopen "
        "one to write the result — never pin a pool connection across an HTTP "
        "round-trip:\n  " + "\n  ".join(offenders)
    )


# ── the detector itself must actually detect ──────────────────────────────────

_BAD = """
async def sweep_once():
    async with SchedulerSessionFactory() as db:
        await db.execute("select 1")
        await provider.fetch_sales(db)   # held across a provider await
"""

_GOOD_COMMIT_THEN_CALL_OUTSIDE = """
async def sweep_once():
    async with SchedulerSessionFactory() as db:
        await db.execute("select 1")
    await provider.fetch_sales(db)       # session already closed
"""

_GOOD_INTERNAL_ONLY = """
async def sweep_once():
    async with SchedulerSessionFactory() as db:
        await some_service.do_work(db)   # internal service, not a client
"""


def _flag(src: str) -> list[int]:
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    return _session_block_third_party_awaits(fn)


def test_detector_flags_a_held_provider_await():
    assert _flag(_BAD), "the guard would miss the very bug it exists to catch"


@pytest.mark.parametrize("src", [_GOOD_COMMIT_THEN_CALL_OUTSIDE, _GOOD_INTERNAL_ONLY])
def test_detector_does_not_flag_safe_shapes(src):
    assert not _flag(src)


# ── the nested case: a session held across a call INTO a service that does HTTP ─
#
# The direct check above catches `await provider....` written straight into a
# session block — the shape a new loop is most likely to grow. It cannot follow a
# call into another module: `await courier_service.retry_failed_dispatches(session)`
# reaches a Lalamove booking two files away, and an AST cannot see that. So this
# second check encodes, BY NAME, the service entrypoints known to hold a session
# across a provider round-trip, and flags a loop that awaits one inside a session
# block.
#
# Unlike the direct check, this one does NOT assert zero. Several such holds are
# deliberately deferred (F-OPS-5) because the leaf interleaves the provider call
# with DB writes AND is shared with the request/webhook path, so a clean
# read → close → HTTP → reopen split would mean rewriting shared, money-critical
# code:
#   * `courier dispatch` holds the delivery row `FOR UPDATE` across the booking
#     that debits the courier wallet — the guard against double-booking one cake
#     (F-COU-3) — and is the admin/POS re-dispatch path too;
#   * `driver_tracking` interleaves `get_order`, `fill_driver_details`,
#     `announce_driver` and `apply_webhook`, the last three shared with the
#     Lalamove webhook handler;
#   * grubops order `_ingest_one` → `ingest` fires the APNS order-placed push
#     between DB writes, on the same create path the storefront uses;
#   * branch-hours `sync_all` → `push_weekly_hours` loads each portal's session
#     cookies FROM the db before the write, and that session store is aggregator
#     territory being rearchitected elsewhere.
#
# So this pins the KNOWN set as a backlog that may only SHRINK — the same shape as
# `test_compose_env_allowlist` and the RSC `fetch-convention` backlog. A NEW hold
# (a listed service appearing under a session in some new loop, or a loop growing
# a call not yet in the backlog) fails; and a hold that gets fixed must be dropped
# from the backlog, or the test fails so the list cannot rot. The limit is
# deliberate: only these named entrypoints are followed. A provider reached
# through a differently-named service is invisible here and still leans on the
# direct check plus review.

#: Service entrypoints — (receiver, method) — whose body awaits a third-party
#: client. A loop that `await`s one of these while a session is open is holding
#: that session across the provider round-trip.
_HTTP_SERVICE_CALLS = {
    ("arrival_service", "sweep"),
    ("courier_service", "retry_failed_dispatches"),
    ("driver_tracking", "refresh_live_drivers"),
    ("grubops_orders_service", "sweep_open_orders"),
    ("foodics_orders_service", "sweep_pending_pushouts"),
}

#: Bare helpers in a loop's own module that fan out into a provider — the local
#: `await _ingest_one(db, ...)` / `await sync_all(db)` shape an attribute match
#: cannot see. Narrow by design; these two names are unique to their loops.
_HTTP_SERVICE_BARE_CALLS = {"_ingest_one", "sync_all"}

#: The nested holds that exist today and are deferred (see the note above). A
#: backlog identified by (path relative to apps/api, enclosing function, dotted
#: call) — no line number, so it survives edits. **It may only shrink.**
# Empty — every known nested hold has been fixed:
#   * branch_hours_sync._tick → sync_all (F-AGG-17): the loop now reads the branch
#     list on a released session and mirrors each branch on its own
#     per-(branch, channel)-committed sessions (`_sync_branch_isolated`), so no
#     session is pinned across the portal PUTs. `sync_all` still exists for the
#     request "Sync now" path, but the loop no longer awaits it under a session.
#   * delivery_scheduler.sweep_once and grubops_orders.sweep_once moved to
#     `advisory_lock.held_session`: their sweep runs on the lock's OWN connection,
#     so there is no separate `SchedulerSessionFactory` session pinned across the
#     Lalamove / GrubOps round-trips — the whole sweep holds one connection, the
#     lock's, which it would hold regardless.
# A new entry may be ADDED here only to defer a genuinely money-critical shared
# leaf (see the note above); it must still only ever shrink.
_KNOWN_NESTED_HOLDS: set[tuple[str, str, str]] = set()


def _is_http_service_call(call: ast.Call) -> bool:
    segments = _dotted_segments(call)
    if not segments:
        return False
    if len(segments) >= 2 and (segments[-2], segments[-1]) in _HTTP_SERVICE_CALLS:
        return True
    return len(segments) == 1 and segments[0] in _HTTP_SERVICE_BARE_CALLS


def _session_block_nested_service_awaits(
    func: ast.AsyncFunctionDef,
) -> list[tuple[int, str]]:
    """(lineno, dotted call) for each known-HTTP service await held in a session
    block."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.AsyncWith):
            continue
        opens_session = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id in SESSION_FACTORY_NAMES
            for item in node.items
        )
        if not opens_session:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Await)
                and isinstance(inner.value, ast.Call)
                and _is_http_service_call(inner.value)
            ):
                hits.append((inner.lineno, ".".join(_dotted_segments(inner.value))))
    return hits


def _nested_offenders_in(path: Path) -> list[tuple[str, str, str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = str(path.relative_to(SERVICES_DIR.parent.parent))
    offenders: list[tuple[str, str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and _is_loop_body(node):
            for lineno, dotted in _session_block_nested_service_awaits(node):
                offenders.append((rel, node.name, dotted, lineno))
    return offenders


def test_no_new_loop_holds_a_session_across_a_known_http_service():
    found: dict[tuple[str, str, str], int] = {}
    for path in sorted(SERVICES_DIR.rglob("*.py")):
        for rel, func, dotted, lineno in _nested_offenders_in(path):
            found[(rel, func, dotted)] = lineno

    keys = set(found)
    new = keys - _KNOWN_NESTED_HOLDS
    assert not new, (
        "A scheduler loop holds a DB session across an await into a service that "
        "does third-party HTTP. Compute the deltas on a short session, close it, "
        "call the service with NO session held, then reopen one to persist — see "
        "`driver_routing.refresh_routes` / `grubops_reconcile._reconcile_branch` "
        "for the shape:\n  "
        + "\n  ".join(
            f"{rel}:{found[(rel, func, dotted)]} (in {func}) → {dotted}"
            for (rel, func, dotted) in sorted(new)
        )
    )
    fixed = _KNOWN_NESTED_HOLDS - keys
    assert not fixed, (
        "These deferred session-across-HTTP holds are gone — remove them from "
        "_KNOWN_NESTED_HOLDS so the backlog only shrinks:\n  "
        + "\n  ".join(
            f"{rel} (in {func}) → {dotted}" for (rel, func, dotted) in sorted(fixed)
        )
    )


# ── the nested detector itself must actually detect ───────────────────────────

_BAD_NESTED = """
async def sweep_once():
    async with SchedulerSessionFactory() as session:
        await courier_service.retry_failed_dispatches(session)
"""

_BAD_NESTED_BARE = """
async def _tick():
    async with SchedulerSessionFactory() as db:
        await sync_all(db)
"""

_GOOD_NESTED_OUTSIDE = """
async def sweep_once():
    async with SchedulerSessionFactory() as db:
        rows = await db.execute("select 1")
    await driver_tracking.refresh_live_drivers()   # session already closed
"""

_GOOD_NESTED_UNLISTED = """
async def sweep_once():
    async with SchedulerSessionFactory() as db:
        await some_service.do_work(db)   # not a known HTTP service
"""


def _flag_nested(src: str) -> list[tuple[int, str]]:
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    return _session_block_nested_service_awaits(fn)


@pytest.mark.parametrize("src", [_BAD_NESTED, _BAD_NESTED_BARE])
def test_nested_detector_flags_a_held_service_await(src):
    assert _flag_nested(src), "the nested guard would miss a known-bad shape"


@pytest.mark.parametrize("src", [_GOOD_NESTED_OUTSIDE, _GOOD_NESTED_UNLISTED])
def test_nested_detector_does_not_flag_safe_shapes(src):
    assert not _flag_nested(src)
