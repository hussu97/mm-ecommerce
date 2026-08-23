"""
Every `commit()` outside the request dependency says why it is there.

The convention is that services `flush()` and the request-scoped `get_db`
dependency commits. Some code has to break it: a courier booking spends real
money outside our transaction, so losing the courier's id to a later rollback
would leave their job running and let the next dispatch book a second rider for
the same cake.

That deviation is correct. What went wrong is how it spread.
`lalamove_service.dispatch_order` carried a nine-line justification;
`slider_service` and `noon_send_service` did the identical thing with no
comment at all — the deviation was copied and the reasoning was not. A reader
then finds three commits, one explained, and no way to tell whether the other
two are considered or accidental.

So the rule this enforces is not "never commit" — the code above needs to. It
is "a commit outside `get_db` is a decision, and a decision is written down".
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

#: The one sanctioned commit: the request-scoped dependency whose whole job
#: this is.
OWNS_THE_TRANSACTION = {APP_DIR / "core" / "deps.py"}

#: How close above the call a comment has to be to count as explaining it.
LOOKBACK_LINES = 10


def _commit_lines(tree: ast.AST) -> list[int]:
    """
    Commits of a session the function was *handed*, not one it opened.

    That is the distinction rule 2 draws. A service that opens its own session
    — `webhook_log_service.Recorder`, the retention sweep, the batch scheduler
    — is the sanctioned side-write case and owns its transaction by
    construction. Committing the caller's session is the deviation, because the
    caller is usually a request and `get_db` was going to commit it.
    """
    found: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = fn.args
        handed = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in handed
            ):
                found.append(node.lineno)
    return found


def _unexplained(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    hits = []
    for lineno in _commit_lines(ast.parse(source)):
        window = lines[max(0, lineno - 1 - LOOKBACK_LINES) : lineno - 1]
        if any(line.lstrip().startswith("#") for line in window):
            continue
        # A docstring immediately above counts too — several of these live in
        # small helpers whose whole docstring is the justification.
        if any('"""' in line for line in window):
            continue
        hits.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}")
    return hits


def test_every_commit_outside_get_db_is_explained():
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path in OWNS_THE_TRANSACTION:
            continue
        offenders.extend(_unexplained(path))

    assert not offenders, (
        "these commit a session without saying why. Services flush and "
        "`get_db` commits; if this one must not wait for the request to end, "
        "say what it is protecting — the last time two of these went "
        "unexplained, the reasoning existed in a third file and nobody could "
        f"tell the copies were deliberate:\n  {chr(10).join(offenders)}"
    )
