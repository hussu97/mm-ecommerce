"""
`AGENTS.md` points at `CLAUDE.md`; it must never become a second copy of it.

It was a copy once. The copy kept the workflow half of `CLAUDE.md` and lost the
entire "System Design Conventions" section — so an agent reading `AGENTS.md`,
which is the file Codex and several other harnesses read by convention, got no
order-lifecycle rule, no transaction rule and no migration rule. It also went
on describing the secret checklist as four locations after the fifth had been
added, which is the pre-outage text: on 2026-08-05 that missing fifth place had
production running with noon Send inert and the bot check off, for weeks.

Nothing warned anybody, because a stale duplicate reads exactly like a current
one. This is the warning.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CLAUDE_MD = ROOT / "CLAUDE.md"
AGENTS_MD = ROOT / "AGENTS.md"

#: Headings that belong to the canon. If one appears in `AGENTS.md`, the fork
#: has started growing back.
CANON_HEADINGS = (
    "System Design Conventions",
    "Workflow Orchestration",
    "Secret/Env Var Checklist",
    "Admin Pagination Standard",
    "Analytics Tracking Rule",
    "Task Management",
    "Core Principles",
)


def test_agents_md_points_at_the_canon():
    body = AGENTS_MD.read_text()

    assert "CLAUDE.md" in body, (
        "AGENTS.md must name CLAUDE.md — it is the only thing it is for"
    )


def test_agents_md_does_not_restate_the_canon():
    body = AGENTS_MD.read_text()
    restated = [
        h for h in CANON_HEADINGS if re.search(rf"^#+ .*{re.escape(h)}", body, re.M)
    ]

    assert not restated, (
        "AGENTS.md has started carrying the canon again instead of pointing at "
        "it. Two copies of a convention is two answers to one question, and the "
        "last copy lost the conventions section entirely while keeping the "
        f"workflow rules:\n  {chr(10).join(restated)}"
    )


def test_the_checklist_is_stated_once_and_says_five():
    """
    The specific sentence that was wrong for weeks.

    Guarded by count as well as by wording: the failure was not that the number
    was hard to find, it was that there were two numbers in two files and the
    stale one looked authoritative.
    """
    canon = CLAUDE_MD.read_text()

    assert re.search(r"ALL \*\*five\*\* locations", canon), (
        "CLAUDE.md no longer states the secret checklist as five locations"
    )
    assert not re.search(r"ALL four locations|four places", AGENTS_MD.read_text()), (
        "AGENTS.md is describing the secret checklist again, and the last time "
        "it did that it said four"
    )
