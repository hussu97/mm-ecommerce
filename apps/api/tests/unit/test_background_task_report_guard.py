"""
Every fire-and-forget `asyncio.create_task` reports the task's death (F-OPS-27).

Convention #5 rules out `BackgroundTasks` in favour of a tracked `asyncio.Task`
held in a module-level set. Holding a reference keeps the task from being GC'd
mid-flight, but it does NOT report a task that *dies*: an exception that escapes
the coroutine body is logged by asyncio at most and never reaches Sentry. The
fix, `app/core/background.py`, adds the missing half — `report_result`, an
`add_done_callback` target that routes a real exception through
`alerting.capture_exc`. The reference services (`image_warm_service`,
`indexnow_service`) had held a reference but attached no such callback, so their
escape-hatch failures were silently lost.

This guard makes that regression un-writable. It is a sibling of
`test_scheduler_session_guard` and enforced the same way — STRUCTURALLY, over the
AST of everything under `app/`:

  every `asyncio.create_task(...)` call, in a function outside
  `app/core/background.py`, must sit in a function that also attaches
  `report_result` as a done-callback — UNLESS the (module, function) is on the
  allow-list of deliberate exceptions below.

The check is intentionally about `asyncio.create_task` specifically (a bare
`provider.create_task(...)` method is a different thing and is not matched) and
about the *reporting* callback specifically (`_pending.discard` keeps the set
tidy but does not report a death). It is function-scoped rather than
variable-scoped: every spawner in this codebase creates exactly one tracked task
per function, so "this function attaches `report_result`" is both sufficient and
robust against the `task = create_task(...); ...; task.add_done_callback(...)`
gap between the two statements.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"

#: The one module allowed to call `asyncio.create_task` without attaching
#: `report_result`: it *is* the reporting machinery (`spawn_tracked` is where the
#: callback is attached for everyone else), so requiring it to report itself is
#: circular.
_EXEMPT_MODULE = APP_DIR / "core" / "background.py"

#: Deliberate exceptions: (module path relative to `app/`, function name) pairs
#: whose `asyncio.create_task` calls are reported by other means and so do not
#: attach `report_result` themselves. Kept BY NAME (no line number) so it
#: survives edits, and it may only SHRINK.
#:
#:   * `run_aggregator_schedulers_forever` spawns its child loops into an
#:     `asyncio.gather(*children)` it then awaits for the whole leader tenure, so
#:     a child that raises propagates into the supervisor (re-election), which is
#:     itself a tracked/reported loop — the death is surfaced through the gather,
#:     not a per-child callback.
_ALLOWED_WITHOUT_REPORT: set[tuple[str, str]] = {
    ("services/aggregators/ingest.py", "run_aggregator_schedulers_forever"),
}


def _is_asyncio_create_task(call: ast.Call) -> bool:
    """True for `asyncio.create_task(...)`, false for a bare `x.create_task(...)`
    method (e.g. a provider's own `create_task`) or an unrelated call."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "create_task"
        and isinstance(func.value, ast.Name)
        and func.value.id == "asyncio"
    )


def _attaches_report_result(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body calls `<task>.add_done_callback(report_result)`."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_done_callback"
            and any(
                isinstance(arg, ast.Name) and arg.id == "report_result"
                for arg in node.args
            )
        ):
            return True
    return False


def _offenders_in(path: Path) -> list[str]:
    """`module:func` for each function that spawns an unreported `create_task`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = str(path.relative_to(APP_DIR))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        spawns = any(
            _is_asyncio_create_task(inner)
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
        )
        if not spawns:
            continue
        if (rel, node.name) in _ALLOWED_WITHOUT_REPORT:
            continue
        if not _attaches_report_result(node):
            offenders.append(f"{rel}:{node.name}")
    return offenders


def test_every_create_task_outside_background_reports_its_death():
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == _EXEMPT_MODULE:
            continue
        offenders.extend(_offenders_in(path))
    assert not offenders, (
        "These functions spawn an `asyncio.create_task` whose death would be "
        "silently lost. Attach the reporting callback — "
        "`task.add_done_callback(report_result)` from `app.core.background` — so "
        "an exception that escapes the task reaches Sentry (see "
        "`image_warm_service.warm_in_background` for the shape), or spawn it via "
        "`spawn_tracked`:\n  " + "\n  ".join(offenders)
    )


def test_allow_list_only_shrinks():
    """Every allow-listed (module, function) must still exist and still spawn an
    unreported task — otherwise the entry is stale and must be dropped so the
    backlog cannot rot."""
    stale: list[tuple[str, str]] = []
    for rel, func_name in _ALLOWED_WITHOUT_REPORT:
        path = APP_DIR / rel
        if not path.exists():
            stale.append((rel, func_name))
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        matched = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == func_name
            and any(
                _is_asyncio_create_task(inner)
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
            )
            and not _attaches_report_result(node)
        ]
        if not matched:
            stale.append((rel, func_name))
    assert not stale, (
        "These allow-list entries no longer spawn an unreported `create_task` — "
        "drop them from `_ALLOWED_WITHOUT_REPORT` so it only shrinks:\n  "
        + "\n  ".join(f"{rel}:{func}" for rel, func in stale)
    )


# ── the detector itself must actually detect ──────────────────────────────────

_BAD = """
import asyncio
async def spawn():
    task = asyncio.create_task(work())
    _pending.add(task)
    task.add_done_callback(_pending.discard)   # tidies the set, does not report
"""

_GOOD_REPORTS = """
import asyncio
async def spawn():
    task = asyncio.create_task(work())
    task.add_done_callback(report_result)
"""

_GOOD_METHOD_NOT_ASYNCIO = """
async def send():
    created = await provider.create_task(payload)   # not asyncio.create_task
    return created
"""


def _flag(src: str) -> list[str]:
    fn = ast.parse(src).body[-1]
    assert isinstance(fn, ast.AsyncFunctionDef)
    spawns = any(
        _is_asyncio_create_task(inner)
        for inner in ast.walk(fn)
        if isinstance(inner, ast.Call)
    )
    return [fn.name] if spawns and not _attaches_report_result(fn) else []


def test_detector_flags_an_unreported_spawn():
    assert _flag(_BAD), "the guard would miss the very bug it exists to catch"


@pytest.mark.parametrize("src", [_GOOD_REPORTS, _GOOD_METHOD_NOT_ASYNCIO])
def test_detector_does_not_flag_safe_shapes(src):
    assert not _flag(src)
