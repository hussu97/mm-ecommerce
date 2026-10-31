"""Work that must wait until the caller's transaction has committed.

**Why this exists.** A Stripe `payment_intent.succeeded` took ~4s at the median
and up to 11s, almost none of it ours. The handler holds the order row
`FOR UPDATE` and — once the order is confirmed — the branch's inventory advisory
lock (`source_event_service.accept_order`), both until the request commits, and
inside that window it awaited two Resend emails and an APNs push to the branch's
registers. Every till sale at that branch that posts stock queued behind a mail
provider's round-trip.

So a side effect that talks to the outside world, and that the transaction does
not depend on, is registered here instead of awaited:

    await post_commit.defer(db, lambda: send(response), name="confirmation:MM-1")

and runs as a tracked background task (`background.spawn_tracked`, the
convention-5 helper) the moment the session's **outermost** transaction commits.

* **Rolled back means never run.** A rollback of the transaction it was
  registered in — or of a savepoint it was registered inside — drops it, so a
  customer is never told about an order that did not happen. A session closed
  without committing drops everything it was still holding.
* **A savepoint is not a commit.** Releasing a `begin_nested()` block keeps the
  work waiting for the outer transaction; only the real `COMMIT` lets it go.
* **Never raises.** Neither into the caller (`defer` only records) nor into the
  commit (the listener only spawns). The work itself is wrapped too: a failure
  is logged and reported, never propagated.
* **Nothing to wait for means now.** With no open transaction on the session —
  or something that is not a SQLAlchemy session at all, which in practice is a
  unit test's mock — there is no commit coming, so the work is awaited inline,
  exactly as it was before this module existed.

**The cost, stated plainly.** Inline work that died with the process took the
transaction with it, and the gateway's retry did everything again. Deferred
work runs after the dedup row is committed, so a process killed in the gap
between `COMMIT` and the send loses the send and a retry will not repeat it.
`drain` (awaited in both processes' shutdown) closes that gap for an orderly
stop — a deploy — and the confirmation emails, the ones that matter, have a
sweep that resends what was lost (`orders.confirmation_email_backstop`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from app.core import background

logger = logging.getLogger(__name__)

__all__ = ["defer", "drain", "pending"]

#: Where a session keeps the work it is holding for its commit.
_KEY = "mm.post_commit"

#: Post-commit tasks still running, so shutdown can wait for them (`drain`).
#: `spawn_tracked` holds its own reference too; this set is only the subset a
#: graceful stop should finish.
_IN_FLIGHT: set[asyncio.Task] = set()


@dataclass(frozen=True, eq=False)
class _Pending:
    #: The innermost transaction open when the work was registered. A rollback
    #: of it, or of anything enclosing it, drops the work.
    transaction: SessionTransaction
    name: str
    work: Callable[[], Awaitable[Any]]


def _session_of(db: Any) -> Session | None:
    """The sync `Session` behind *db*, or None for anything that is not one."""
    session = getattr(db, "sync_session", db)
    return session if isinstance(session, Session) else None


async def defer(db: Any, work: Callable[[], Awaitable[Any]], *, name: str) -> None:
    """
    Run `work()` once *db*'s outermost transaction commits; never if it does not.

    *work* is a zero-argument callable returning an awaitable — not a coroutine
    object — so work that is dropped on a rollback never leaves an un-awaited
    coroutine behind. Whatever it closes over must not need *db*: by the time it
    runs, that session belongs to the next request. Build the payload (an
    `OrderResponse`, a push body) first, inside the transaction, and defer only
    the network call.

    Async only for the inline case — see the module docstring.
    """
    session = _session_of(db)
    transaction = None
    if session is not None:
        transaction = session.get_nested_transaction() or session.get_transaction()
    if session is None or transaction is None:
        await _run(work, name)
        return
    session.info.setdefault(_KEY, []).append(_Pending(transaction, name, work))


def pending(db: Any) -> list[str]:
    """Names of the work *db* is holding for its commit. For tests and logs."""
    session = _session_of(db)
    if session is None:
        return []
    return [p.name for p in session.info.get(_KEY, ())]


async def drain(timeout: float = 10.0) -> None:
    """Wait (bounded) for post-commit work already running. Called on shutdown,
    so a deploy that stops the process a second after a payment does not take
    the customer's confirmation email with it."""
    tasks = [t for t in _IN_FLIGHT if not t.done()]
    if not tasks:
        return
    logger.info("Waiting for %d post-commit task(s) before shutdown", len(tasks))
    _done, still = await asyncio.wait(tasks, timeout=timeout)
    if still:
        logger.warning(
            "%d post-commit task(s) still running at shutdown: %s",
            len(still),
            ", ".join(sorted(t.get_name() for t in still)),
        )


async def _run(work: Callable[[], Awaitable[Any]], name: str) -> None:
    try:
        await work()
    except Exception:  # noqa: BLE001 — after the commit there is nobody to tell
        logger.exception("Post-commit work %s failed", name)


def _spawn(item: _Pending) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — a sync session never defers
        logger.error("No event loop to run post-commit work %s; dropped", item.name)
        return
    task = background.spawn_tracked(
        _run(item.work, item.name), name=f"post-commit:{item.name}"
    )
    _IN_FLIGHT.add(task)
    task.add_done_callback(_IN_FLIGHT.discard)


def _encloses(outer: SessionTransaction, inner: SessionTransaction) -> bool:
    """Whether *inner* is *outer* or was begun inside it."""
    node: SessionTransaction | None = inner
    while node is not None:
        if node is outer:
            return True
        node = node.parent
    return False


# Registered on the `Session` class, so every session — the request's, a
# scheduler sweep's, one bound to an advisory-lock connection — behaves the
# same. Each is a dictionary lookup for a session that deferred nothing.


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    # Fired for a released savepoint as well as for the real commit. A savepoint
    # is still inside a transaction that can roll back, so the work waits on.
    if session.in_nested_transaction():
        return
    for item in session.info.pop(_KEY, None) or ():
        _spawn(item)


@event.listens_for(Session, "after_soft_rollback")
def _after_soft_rollback(
    session: Session, previous_transaction: SessionTransaction
) -> None:
    held = session.info.get(_KEY)
    if not held:
        return
    kept = []
    for item in held:
        if _encloses(previous_transaction, item.transaction):
            logger.info("Rolled back; post-commit work %s dropped", item.name)
        else:
            kept.append(item)
    session.info[_KEY] = kept


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    # The outermost transaction is over. A commit has already spawned and
    # cleared its work above; anything still here was never committed — a
    # rollback, or a session closed with its transaction open.
    if transaction.parent is None:
        for item in session.info.pop(_KEY, None) or ():
            logger.info("Not committed; post-commit work %s dropped", item.name)
