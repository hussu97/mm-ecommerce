"""
Cheap change detection for the register's order polls (`GET /pos/orders`,
`GET /pos/orders/{id}`).

Every iPad polls the order list three or four times a minute — the incoming
queue every 20 s, the board and its pay-later tab every 45 s, the return docket
every 2 min — plus one `GET /pos/orders/{id}` per watched driver slip every 30 s.
Nearly all of those answer "nothing changed", and each one paid for an auth
lookup, ~14 eager-load round trips and Pydantic over up to 200 orders to say so.
Now a terminal that opts in gets an ETag, sends it back as `If-None-Match`, and
an unchanged answer is a bodiless 304 decided before any order is loaded.

**What "changed" means.** Every committed ORM write that touches a table the
response is built from bumps a generation in Redis:

* the order tables (`SCOPED_TABLES`) bump **that order's branch** — the branch
  is read off the `Order` row in the flush, or off the session's identity map
  for a child row (a line, a payment, the delivery). A child row whose order is
  not in the session, and any bulk `update()`/`delete()` through the session,
  cannot be attributed and bumps every branch instead (`UNSCOPED`);
* the tables every order reads through a relationship (`SHARED_TABLES`: the
  branch, the legal entity) bump every branch too.

The write is seen by SQLAlchemy session events — the same seam
`counter_bundle_cache` uses — not by a call each write path has to remember, so
the register's routes, a storefront payment webhook, the aggregator promote
sweep, a courier's driver push and an admin edit are all covered without
knowing this module exists. Adding a relationship to the order response means
adding its table here; `test_pos_orders_etag.py` diffs what the endpoint reads
against these sets.

**What it cannot see, and the ceiling that covers it.** Raw SQL (`text()`), a
direct prod fix, a migration, a lost Redis INCR. Every ETag therefore carries
its own expiry — at most `TTL_CEILING` after it was issued, and sooner when the
body holds a driver distance, which `driver_proximity` withdraws once the
position is `MAX_AGE` old — and a token past its expiry is never answered 304.

**Never wrong, only slower.** Without Redis there is no generation, no ETag
and no 304: every request is served exactly as before. A client that does not
opt in (`X-POS-ETag: 1`) gets no ETag at all, so an older register — whose
URLSession cache would otherwise start revalidating on its own — is untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterable
from datetime import timezone
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.util import identity_key

from app.core.background import spawn_tracked
from app.core.cache import cache_get, cache_incr
from app.models import Order
from app.schemas.pos_order import PosOrderResponse
from app.services.delivery.driver_proximity import MAX_AGE

logger = logging.getLogger(__name__)

__all__ = [
    "OPT_IN_HEADER",
    "SCOPED_TABLES",
    "SHARED_TABLES",
    "TTL_CEILING",
    "UNSCOPED",
    "bump",
    "detail_key",
    "list_key",
    "make_etag",
    "matching_etag",
    "valid_until",
    "wants_etag",
]

#: The request header a register sends to be given ETags. Without it nothing
#: here runs and the response is byte-for-byte what it always was.
OPT_IN_HEADER = "X-POS-ETag"

#: Tables keyed to one order: a write bumps that order's branch.
SCOPED_TABLES: frozenset[str] = frozenset(
    {
        "orders",
        "order_items",
        "order_payments",
        "order_charges",
        "order_discounts",
        "order_taxes",
        "order_deliveries",
        "marketplace_returns",
    }
)

#: Tables an order reads through a relationship that many orders share: a
#: write bumps every branch.
SHARED_TABLES: frozenset[str] = frozenset({"branches", "legal_entities"})

#: The longest any ETag is honoured, whatever the generation says (seconds).
TTL_CEILING = 300

#: Redis keys. `any` moves on every write; `unscoped` on every write that could
#: not be pinned to a branch; `b:{id}` on writes to that branch's orders.
_KEY_ANY = "pos:orders:gen:any"
_KEY_UNSCOPED = "pos:orders:gen:unscoped"
_KEY_BRANCH = "pos:orders:gen:b:{}"

#: Marker in the session's touched-set for a write no branch can be named for.
UNSCOPED = "*"

_TOUCHED = "_pos_orders_touched"

#: The response's shape. Hashed into every ETag so a deploy that changes the
#: schema retires every token at once instead of within `TTL_CEILING`.
_SHAPE = hashlib.sha1(
    json.dumps(PosOrderResponse.model_json_schema(), sort_keys=True).encode()
).hexdigest()[:12]


# ─── Seeing writes ────────────────────────────────────────────────────────────


def _table_name(obj: Any) -> str | None:
    table = getattr(type(obj), "__table__", None)
    return getattr(table, "name", None)


def _branches_of(session: Session, obj: Any, table: str) -> set[Any]:
    """Which branches' order lists this flushed row can change."""
    if table in SHARED_TABLES:
        return {UNSCOPED}
    if isinstance(obj, Order):
        found: set[Any] = {obj.branch_id}
        # An order moved between branches leaves one list and joins another.
        history = inspect(obj).attrs.branch_id.history
        found.update(history.deleted or ())
        return {b if b is not None else UNSCOPED for b in found}
    order_id = getattr(obj, "order_id", None)
    if order_id is not None:
        order = session.identity_map.get(identity_key(Order, order_id))
        if order is not None and order.branch_id is not None:
            return {order.branch_id}
    return {UNSCOPED}


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _flush_context: Any) -> None:
    # `new`/`dirty`/`deleted` and attribute history still hold the pre-flush
    # state here.
    touched: set[Any] | None = None
    for obj in (*session.new, *session.dirty, *session.deleted):
        table = _table_name(obj)
        if table not in SCOPED_TABLES and table not in SHARED_TABLES:
            continue
        if touched is None:
            touched = session.info.setdefault(_TOUCHED, set())
        touched |= _branches_of(session, obj, table)


@event.listens_for(Session, "do_orm_execute")
def _do_orm_execute(state: Any) -> None:
    # A bulk `update(OrderItem)` through the session never passes through a
    # flush, and names no single order.
    if not (state.is_update or state.is_delete or state.is_insert):
        return
    table = getattr(getattr(state.statement, "table", None), "name", None)
    if table in SCOPED_TABLES or table in SHARED_TABLES:
        state.session.info.setdefault(_TOUCHED, set()).add(UNSCOPED)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    # Only the outermost commit makes the write visible to a poll; bumping on a
    # savepoint release would let a poll cache the old rows under the new
    # generation.
    if session.in_nested_transaction():
        return
    touched = session.info.pop(_TOUCHED, None)
    if touched:
        bump(touched)


def bump(branches: Iterable[Any]) -> None:
    """Retire the ETags of these branches (`UNSCOPED` for all of them)."""
    keys = [_KEY_ANY]
    for branch in set(branches):
        keys.append(_KEY_UNSCOPED if branch == UNSCOPED else _KEY_BRANCH.format(branch))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # a sync script: no Redis to tell, and the ceiling covers it
    spawn_tracked(_incr_all(keys), name="pos-orders-generation")


async def _incr_all(keys: list[str]) -> None:
    for key in keys:
        await cache_incr(key)


# ─── Reading the generation ───────────────────────────────────────────────────


async def _read(key: str) -> int | None:
    """The counter's value, or None when Redis cannot be reached.

    A key nobody has bumped yet (a fresh Redis, an evicted key) reads as absent
    from `cache_get` just as an outage does; incrementing it tells the two
    apart — an outage cannot increment — and a spurious bump only costs one
    full response.
    """
    value = await cache_get(key)
    if value is None:
        return await cache_incr(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _generations(branch_id: uuid.UUID | None) -> tuple[int, ...] | None:
    if branch_id is None:
        keys = [_KEY_ANY]
    else:
        keys = [_KEY_BRANCH.format(branch_id), _KEY_UNSCOPED]
    values = await asyncio.gather(*(_read(k) for k in keys))
    if any(v is None for v in values):
        return None
    return tuple(values)  # type: ignore[arg-type]


def _digest(*parts: Any) -> str:
    raw = json.dumps([_SHAPE, *parts], default=str, separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


async def list_key(
    branch_id: uuid.UUID | None, query: Iterable[tuple[str, str]]
) -> str | None:
    """What an unchanged list answer is identified by: the generations of the
    branch it is scoped to (every branch when it names none) and the exact
    query. None without Redis, and then there is no ETag."""
    gens = await _generations(branch_id)
    if gens is None:
        return None
    return _digest("list", gens, sorted(query))


async def detail_key(order_id: uuid.UUID, branch_id: uuid.UUID | None) -> str | None:
    """The same for one order, scoped to its branch."""
    gens = await _generations(branch_id)
    if gens is None:
        return None
    return _digest("detail", str(order_id), gens)


# ─── The token ────────────────────────────────────────────────────────────────


def valid_until(orders: Iterable[Order], *, now: float | None = None) -> int:
    """When a body built now could start to differ with nothing written.

    `driver_distance_km` is the one time-relative field: `driver_proximity`
    withdraws a position (and separately a route) once it is `MAX_AGE` old. So
    a body that may carry one expires when the youngest stamp it could be
    quoting crosses that age. Only loaded deliveries count — a response built
    without the branch never quotes a distance.
    """
    now = time.time() if now is None else now
    until = now + TTL_CEILING
    max_age = MAX_AGE.total_seconds()
    for order in orders:
        state = inspect(order)
        if "delivery" in state.unloaded or "branch" in state.unloaded:
            continue
        delivery = order.delivery
        if delivery is None:
            continue
        for stamp in (delivery.driver_location_at, delivery.driver_route_at):
            if stamp is None:
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            expires = stamp.timestamp() + max_age
            if expires > now:
                until = min(until, expires)
    return int(until)


def make_etag(key: str, until: int) -> str:
    return f'W/"{key}.{until}"'


def matching_etag(
    if_none_match: str | None, key: str, *, now: float | None = None
) -> str | None:
    """The client's token that still names `key` and has not expired, if any."""
    if not if_none_match:
        return None
    now = time.time() if now is None else now
    for candidate in if_none_match.split(","):
        token = candidate.strip()
        bare = token[2:] if token.startswith("W/") else token
        bare = bare.strip('"')
        tag, _, until = bare.rpartition(".")
        if tag != key:
            continue
        try:
            if int(until) > now:
                return token
        except ValueError:
            continue
    return None


def wants_etag(headers: Any) -> bool:
    """Whether the request opted in. Only a register that handles 304 asks."""
    return headers.get(OPT_IN_HEADER) == "1"
