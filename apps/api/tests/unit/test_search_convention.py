"""Every search box matches the same way: trimmed, case-insensitive, anywhere.

`app.core.search.contains` is the one place that builds a `%…%` pattern. A
hand-rolled `f"%{q}%"` forgets to escape `%`/`_` (so `50%` matches every row)
or to trim — which is how the admin and till searches drifted apart.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import column
from sqlalchemy.dialects import postgresql

from app.core import search

APP = Path(__file__).resolve().parents[2] / "app"
HAND_ROLLED = re.compile(r"""f["']%\{""")


def test_no_hand_rolled_contains_patterns():
    offenders = [
        f"{path.relative_to(APP)}:{number}"
        for path in APP.rglob("*.py")
        if path.name != "search.py"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if HAND_ROLLED.search(line)
    ]
    assert offenders == [], (
        "Build search patterns with app.core.search.contains, not by hand: "
        + ", ".join(offenders)
    )


def _compiled(clause) -> tuple[str, dict]:
    compiled = clause.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_contains_is_trimmed_case_insensitive_and_anywhere():
    sql, params = _compiled(search.contains(column("name"), "  Dark Choc \n"))
    assert "ILIKE" in sql.upper()
    assert list(params.values()) == ["%Dark Choc%"]


def test_contains_neutralises_wildcards():
    _, params = _compiled(search.contains(column("name"), " 50%_off "))
    assert list(params.values()) == ["%50\\%\\_off%"]
