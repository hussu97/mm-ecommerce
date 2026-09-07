"""Per-order transaction isolation for the aggregator sweeps.

Every aggregator pass — ingest, promote, reconcile — walks a batch of orders in
one transaction and must not let one poison order take the rest down with it. A
bare ``try/except`` cannot do that under asyncpg: the driver aborts the WHOLE
transaction on the first failure, so every later statement then fails with
"current transaction is aborted", the batch's counter lands at 0, and the whole
pass rolls back — while the logs blame each order individually.

A ``SAVEPOINT`` per order (``db.begin_nested()``) rolls back just the bad row and
leaves the surrounding transaction usable, so orders before and after it still
commit. The one class of error a per-order savepoint must NOT swallow is the one
that is wrong for EVERY row — a lost connection, a pool timeout, a schema
mismatch — because isolating that would write 0 and let the sweep report itself
"completed" over a real outage. ``_SYSTEMIC_DB_ERRORS`` names exactly those
classes so the callers can re-raise them and fail the run honestly.

Kept in one module so the ingest, promote and reconcile passes share the same
tuple rather than each defining its own and drifting.
"""

from __future__ import annotations

from sqlalchemy.exc import (
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

#: DB errors that mean the connection/schema is wrong for every row, so a
#: per-order savepoint must NOT swallow them (that would silently write 0 and mark
#: the run completed). Everything else — IntegrityError, DataError, a parse
#: ValueError — is per-order and isolated. Kept narrow on purpose: only "the whole
#: write path is broken" classes belong here.
_SYSTEMIC_DB_ERRORS = (
    OperationalError,  # connection lost, pool timeout, server shutting down
    InterfaceError,  # connection already closed / protocol error
    InternalError,  # "current transaction is aborted" and peers
    ProgrammingError,  # undefined column/table, bad SQL — a schema mismatch
)
