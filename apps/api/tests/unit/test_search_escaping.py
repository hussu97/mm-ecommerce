"""
A search box is a literal, not a pattern.

There were four byte-identical private copies of this escaping — in
`order_service`, `product_service`, `users` and very nearly in the new live-cart
listing. That is the shape `docs/architecture-audit-2026-08.md` catalogues, and
the way it drifts is silent: a copy that forgets the backslash, or a call site
that escapes the string and then omits `escape="\\"`, turns a customer searching
for `50%` into a query that matches every row in the table and looks like it
worked.

One copy now, in `app.core.search`, and the pair that has to travel together —
the escaped pattern and the `ESCAPE` clause that makes it mean anything — is
one function.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.core.search import contains, escape_like
from app.models.user import User


def test_the_wildcards_are_neutralised():
    assert escape_like("50%") == "50\\%"
    assert escape_like("a_b") == "a\\_b"


def test_the_backslash_is_escaped_first():
    """
    Escaping it after the wildcards would escape the escapes, and `\\%` would
    come back out as a literal backslash followed by a live wildcard.
    """
    assert escape_like("\\") == "\\\\"
    assert escape_like("\\%") == "\\\\\\%"


def test_contains_carries_its_own_escape_clause():
    """
    The half that is easy to leave behind. Without `ESCAPE`, PostgreSQL never
    learns that the backslashes above mean anything, and the escaping is worse
    than useless — the pattern now contains literal backslashes as well.
    """
    sql = str(
        contains(User.email, "50%").compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "ILIKE" in sql
    assert "ESCAPE" in sql


def test_an_ordinary_search_is_left_alone():
    assert escape_like("alice@example.com") == "alice@example.com"
