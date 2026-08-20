"""
Turning what somebody typed into a search box into something `ILIKE` can hold.

One function, and it is here because there were four copies of it — byte for
byte identical, private to `order_service`, `product_service`, `users` and
(nearly) `analytics`. That is exactly the shape the 2026-08 audit catalogued:
a convention that exists four times is a convention that will eventually exist
four *different* ways, and the way this one drifts is silent. A copy that
forgets the backslash, or is used without `escape="\\\\"` at the call site,
turns a customer searching for `50%` into a query that matches every row in the
table and looks like it worked.
"""

from __future__ import annotations

__all__ = ["escape_like", "contains"]


def escape_like(value: str) -> str:
    """
    A literal string, safe to drop between `%` signs.

    The backslash goes first — escaping it after the wildcards would escape the
    escapes. Every caller must pass `escape="\\\\"` to `ilike`, which is what
    `contains` below is for.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains(column, value: str):
    """
    `column ILIKE '%value%'`, with the wildcards in *value* neutralised.

    The pair that has to travel together — the escaped pattern and the `escape`
    argument that tells PostgreSQL how to read it — expressed once so a call
    site cannot get one without the other.
    """
    return column.ilike(f"%{escape_like(value)}%", escape="\\")
