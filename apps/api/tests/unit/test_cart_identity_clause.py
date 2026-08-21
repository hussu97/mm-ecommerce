"""
`identity_clause` must not match a row by its NULLs.

The clause used to end `else Cart.session_id == session_id`, and for a request
carrying no session id that parameter is `None` — which SQLAlchemy compiles to
`session_id IS NULL`. A `carts` row with no identity therefore was not an
orphan; it was a basket that every session-less shopper matched at once, and
production accumulated four different customers' items in one over five months.

Asserted on the compiled SQL rather than through a query, because the failure
was never that the wrong row came back from a fixture — it was the shape of the
predicate itself.
"""

from __future__ import annotations

import uuid

from app.services.cart_service import identity_clause


def _sql(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_anonymous_request_matches_nothing():
    assert _sql(identity_clause(None, None)) == "false"


def test_blank_session_id_matches_nothing():
    """An empty header is not an identity either — see migration 117."""
    assert _sql(identity_clause(None, "")) == "false"


def test_session_id_matches_on_session():
    assert _sql(identity_clause(None, "sess_abc")) == "carts.session_id = 'sess_abc'"


def test_user_takes_precedence_over_session():
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    sql = _sql(identity_clause(user_id, "sess_abc"))
    assert "carts.user_id" in sql
    assert "session_id" not in sql
