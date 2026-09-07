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
