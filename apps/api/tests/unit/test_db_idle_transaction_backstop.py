"""The connection-level backstop against a leaked open transaction.

Twice now a code path has opened a transaction, written a row and then awaited
something external without committing — and both times the pool saturated and the
API started returning 503. On 2026-09-06 the leaked transaction sat open for three
hours, because Postgres had no `idle_in_transaction_session_timeout` and nothing
else reaps one. These pin the backstop so it cannot quietly go away.
"""

from __future__ import annotations

from app.core.database import _connect_args


def test_asyncpg_connections_carry_an_idle_in_transaction_timeout():
    args = _connect_args("postgresql+asyncpg://mm_user:pw@postgres:5432/mm_ecommerce")
    timeout = args["server_settings"]["idle_in_transaction_session_timeout"]
    # Milliseconds, as a string — asyncpg passes server_settings through verbatim.
    assert timeout.isdigit()
    ms = int(timeout)
    # Long enough that no honest transaction here is at risk (services flush and the
    # request-scoped `get_db` commits), short enough that a leak is a blip.
    assert 10_000 <= ms <= 300_000


def test_no_statement_or_lock_timeout_is_smuggled_in():
    """Deliberately absent: both abort work that is making PROGRESS, which is not
    what either outage needed, and a `statement_timeout` would cut short a long
    migration or report. Only an IDLE transaction is ever killed."""
    settings = _connect_args("postgresql+asyncpg://x/y")["server_settings"]
    assert "statement_timeout" not in settings
    assert "lock_timeout" not in settings


def test_non_asyncpg_urls_get_no_server_settings():
    """`server_settings` is an asyncpg concept — the dev default and any sync or
    sqlite URL must not blow up on it."""
    assert _connect_args("sqlite+aiosqlite:///:memory:") == {}
    assert _connect_args("postgresql://mm_user:pw@localhost/mm") == {}
