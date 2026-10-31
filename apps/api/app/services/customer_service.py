"""Build and read the cached cross-channel customer directory.

Statement triggers on ``users`` and ``orders`` append to
``customer_cache_changes``; they never touch a shared row, so writers do not
wait on one another (migration 317 -- the old dirty-flag ``UPDATE`` serialised
every order write in the system). The rebuild runs in the scheduler process
(`run_forever`), never in a request: it folds the queue into
``customer_cache_state.dirty`` and rebuilds when a reader has asked for a fresh
directory (`request_rebuild`) or the cache is older than `_MAX_STALENESS`.
Readers serve the cache as it stands.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.core.cache import cache_delete, cache_get, cache_set
from app.core.phone import describe_phone
from app.models.customer_cache import (
    CustomerCache,
    CustomerCacheState,
    CustomerDeliveryAreaCache,
    CustomerDeliveryAreaPolygonCacheState,
    CustomerOrderCache,
)
from app.models.order import Order, OrderStatusEnum
from app.models.user import User

logger = logging.getLogger(__name__)

_SPACE = re.compile(r"\s+")
_CUSTOMER_NAMESPACE = uuid.UUID("71af5e62-7fdd-4ff7-923e-e274328a56d7")


def _clean(value: str | None) -> str | None:
    value = _SPACE.sub(" ", (value or "").strip())
    return value or None


def normalise_customer_name(value: str | None) -> str | None:
    """Make a customer name stable before it becomes an identity or display value.

    Marketplace and register feeds are inconsistent about casing (``AISHA
    KHAN``, ``aisha khan``), while people expect one readable spelling in the
    directory. Title casing after whitespace cleanup gives the cache one
    canonical display value and means its identity key is based on the exact
    same normalised input. Non-Latin text is left intact by ``str.title``.
    """
    value = _clean(value)
    return value.title() if value else None


def _name_key(value: str | None) -> str | None:
    value = normalise_customer_name(value)
    return value.casefold() if value else None


def _email(value: str | None) -> str | None:
    value = _clean(value)
    # POS orders deliberately carry an empty string to satisfy the legacy
    # non-null web column. A contact without an @ is not an email identity.
    return value.casefold() if value and "@" in value and " " not in value else None


def _unmasked(value: str | None) -> str | None:
    """A contact value, or None when a marketplace masked it.

    Keeta sent ``***`` for the name and ``52*****98`` for the number on every
    order from July to early September 2026. A masked value identifies nobody: as
    a name + phone key it merged unrelated people whose four visible digits
    matched, and it filled the directory with 534 ``***`` "customers". Read as
    absent, such an order has no identity and is left out like an anonymous
    counter sale.
    """
    value = _clean(value)
    return None if value and "*" in value else value


def _order_name(order: Order) -> str | None:
    """Use the delivery snapshot when checkout has no pickup contact."""
    name = normalise_customer_name(_unmasked(order.customer_name))
    if name:
        return name
    snapshot = order.shipping_address_snapshot or {}
    return normalise_customer_name(
        _unmasked(
            " ".join(str(snapshot.get(k) or "") for k in ("first_name", "last_name"))
        )
    )


def _uae_coordinates(snapshot: dict | None) -> tuple[Decimal, Decimal] | None:
    """The coordinate spellings marketplace and website snapshots use, safely.

    A map pin outside the country is not a customer delivery location. Keeping
    that test here as well as in aggregator enrichment protects historical rows
    that predate the enrichment path and avoids a stray geocoder result making
    the UAE map frame itself around another country.
    """
    if not isinstance(snapshot, dict):
        return None
    try:
        latitude = Decimal(str(snapshot.get("latitude", snapshot.get("lat"))))
        longitude = Decimal(str(snapshot.get("longitude", snapshot.get("lng"))))
    except (InvalidOperation, TypeError, ValueError):
        return None
    # Noon Food persists the same coordinates as E7 integers in historic OMS
    # snapshots. Decimal degrees cannot exceed 180, so magnitude is a stable
    # discriminator without needing a channel-specific cache schema.
    if abs(latitude) > 180:
        latitude /= Decimal("10000000")
    if abs(longitude) > 180:
        longitude /= Decimal("10000000")
    if not (Decimal("22.5") <= latitude <= Decimal("26.5")):
        return None
    if not (Decimal("51.3") <= longitude <= Decimal("56.7")):
        return None
    return latitude, longitude


@dataclass
class _Source:
    key: str
    name: str | None
    email: str | None
    phone: str | None
    phone_country: str | None
    user_id: uuid.UUID | None
    observed_at: datetime
    order: Order | None = None

    @property
    def name_key(self) -> str | None:
        return _name_key(self.name)


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def join(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _components(sources: list[_Source]) -> Iterable[list[_Source]]:
    """Join only a stable account or name + shared contact identity.

    A name alone is never an identity (there are many Sarahs), and a shared
    household number/email without a matching name must not silently merge two
    people.  Account membership is the one explicit first-party relationship.
    """
    union = _UnionFind(len(sources))
    by_user: dict[uuid.UUID, int] = {}
    by_name_phone: dict[tuple[str, str], int] = {}
    by_name_email: dict[tuple[str, str], int] = {}

    for index, source in enumerate(sources):
        if source.user_id is not None:
            previous = by_user.setdefault(source.user_id, index)
            union.join(index, previous)

        name = source.name_key
        phone = source.phone
        email = _email(source.email)
        if name and phone:
            key = (name, phone)
            previous = by_name_phone.setdefault(key, index)
            union.join(index, previous)
        if name and email:
            key = (name, email)
            previous = by_name_email.setdefault(key, index)
            union.join(index, previous)

    groups: dict[int, list[_Source]] = defaultdict(list)
    for index, source in enumerate(sources):
        groups[union.find(index)].append(source)
    return groups.values()


def _latest_value(sources: list[_Source], attribute: str) -> str | None:
    for source in sorted(sources, key=lambda item: item.observed_at, reverse=True):
        value = getattr(source, attribute)
        if value:
            return value
    return None


#: Set by a directory read, consumed by the scheduler's next pass. A Redis flag,
#: not a row: a request must never wait on (or lock against) the rebuild.
_REBUILD_WANTED_KEY = "customer_cache:rebuild_wanted"
#: How old a changed-but-unviewed directory may grow before the scheduler
#: rebuilds it anyway -- so the first read after a quiet spell is not hours
#: stale, and the change queue never grows without bound.
_MAX_STALENESS = timedelta(minutes=30)
_TICK_SECONDS = 15
#: A wedged rebuild must not pin the scheduler connection.
_TICK_BUDGET_SECONDS = 300
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4816

#: The order columns a rebuild reads. A full `select(Order)` hydrated every
#: column and fired the model's selectin relationships for ~10k orders.
_ORDER_COLUMNS = (
    Order.id,
    Order.user_id,
    Order.email,
    Order.customer_name,
    Order.customer_phone,
    Order.customer_phone_country,
    Order.shipping_address_snapshot,
    Order.status,
    Order.total,
    Order.created_at,
    Order.source,
    Order.aggregator_channel,
)


async def request_rebuild() -> None:
    """Ask the scheduler for a fresh directory. Never blocks, never raises."""
    await cache_set(_REBUILD_WANTED_KEY, 1, ttl=3600)


async def refresh(db: AsyncSession, *, force: bool = False) -> bool:
    """One scheduler pass: fold queued source changes, and rebuild when wanted.

    Returns whether it rebuilt. Correct under concurrent writers: a change row is
    deleted here only once its writer has committed, and the rebuild's reads
    start after that delete, so they see every change it consumed. A writer
    still in flight keeps its row and dirties the next pass.
    """
    state = await db.scalar(
        select(CustomerCacheState)
        .where(CustomerCacheState.id.is_(True))
        .with_for_update()
    )
    if state is None:
        # The migration seeds this row. Refusing to return a partial directory if
        # an operator has manually damaged it is safer than pretending it is empty.
        raise RuntimeError("customer cache state is missing")
    folded = await db.scalar(
        text(
            "WITH gone AS (DELETE FROM customer_cache_changes RETURNING 1) "
            "SELECT count(*) FROM gone"
        )
    )
    if folded:
        state.dirty = True
    if not state.dirty:
        return False

    # Every rebuild re-inserts the whole directory, so its newest row is when
    # it was last rebuilt (None: never, or nobody to list).
    rebuilt_at = await db.scalar(select(func.max(CustomerCache.created_at)))
    wanted = (
        force
        or rebuilt_at is None
        or datetime.now(timezone.utc) - rebuilt_at >= _MAX_STALENESS
        or await cache_get(_REBUILD_WANTED_KEY) is not None
    )
    if not wanted:
        await db.flush()
        return False
    # Cleared before rebuilding: a read that lands mid-rebuild re-arms it.
    await cache_delete(_REBUILD_WANTED_KEY)
    await _rebuild(db)
    state.dirty = False
    await db.flush()
    return True


async def run_forever() -> None:
    """The directory rebuild loop (scheduler process only)."""
    logger.info("Customer directory loop started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            await heartbeat.beat("customer_cache")
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="customer directory"
            ) as db:
                if db is not None:
                    async with asyncio.timeout(_TICK_BUDGET_SECONDS):
                        await refresh(db)
                        await db.commit()
        except asyncio.CancelledError:
            logger.info("Customer directory loop stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad pass must not kill the loop
            logger.exception("Customer directory rebuild failed")
        await asyncio.sleep(_TICK_SECONDS)


async def _rebuild(db: AsyncSession) -> None:
    """Replace the directory, its order membership and delivery points."""
    orders = (await db.execute(select(*_ORDER_COLUMNS))).all()
    accounts = (
        await db.scalars(
            select(User).where(
                User.is_guest.is_(False),
                User.is_admin.is_(False),
                User.is_staff.is_(False),
            )
        )
    ).all()

    sources: list[_Source] = []
    for account in accounts:
        # An account can be a customer without an order. Its id keeps any order
        # it later makes with that account in the same component even if the
        # account has no display name.
        sources.append(
            _Source(
                key=f"user:{account.id}",
                name=normalise_customer_name(account.display_name),
                email=_email(account.email),
                phone=describe_phone(account.phone).e164 or _clean(account.phone),
                phone_country=getattr(account, "phone_country", None),
                user_id=account.id,
                observed_at=account.updated_at,
            )
        )

    for order in orders:
        name = _order_name(order)
        email = _email(_unmasked(order.email))
        raw_phone = _unmasked(order.customer_phone)
        phone_parts = describe_phone(raw_phone)
        phone = phone_parts.e164 or raw_phone
        # An anonymous counter check has neither a name nor an identity. Do not
        # let it manufacture a row full of dashes in the customer directory.
        if not name and not email and not phone:
            continue
        sources.append(
            _Source(
                key=f"order:{order.id}",
                name=name,
                email=email,
                phone=phone,
                phone_country=phone_parts.country or order.customer_phone_country,
                user_id=order.user_id,
                observed_at=order.created_at,
                order=order,
            )
        )

    await db.execute(delete(CustomerDeliveryAreaCache))
    await db.execute(delete(CustomerOrderCache))
    await db.execute(delete(CustomerCache))
    polygon_state = await db.get(CustomerDeliveryAreaPolygonCacheState, True)
    if polygon_state is not None:
        # Point rows are being replaced below. Their live-zone membership is a
        # separate derived cache and must be remapped, without re-geocoding.
        polygon_state.dirty = True

    customers: list[dict] = []
    memberships: list[dict] = []
    points: list[dict] = []
    for component in _components(sources):
        component_key = "|".join(sorted(source.key for source in component))
        customer_id = uuid.uuid5(_CUSTOMER_NAMESPACE, component_key)
        order_sources = [source for source in component if source.order is not None]
        billable = [
            source.order
            for source in order_sources
            if source.order is not None
            and source.order.status != OrderStatusEnum.CANCELLED
        ]
        total_revenue = sum((Decimal(order.total) for order in billable), Decimal("0"))
        order_count = len(billable)
        dates = [order.created_at for order in billable]
        customers.append(
            {
                "id": customer_id,
                "name": _latest_value(component, "name"),
                "email": _latest_value(component, "email"),
                "phone": _latest_value(component, "phone"),
                "phone_country": _latest_value(component, "phone_country"),
                "order_count": order_count,
                "earliest_order_at": min(dates) if dates else None,
                "latest_order_at": max(dates) if dates else None,
                "total_revenue": total_revenue,
                "aov": (total_revenue / order_count) if order_count else Decimal("0"),
            }
        )
        for source in order_sources:
            memberships.append(
                {"customer_id": customer_id, "order_id": source.order.id}
            )
            # Counter/pickup orders have no useful drop-off, and a cancelled
            # order neither belongs in customer revenue nor in delivery demand.
            if source.order.status == OrderStatusEnum.CANCELLED:
                continue
            coordinates = _uae_coordinates(source.order.shipping_address_snapshot)
            if coordinates is None:
                continue
            latitude, longitude = coordinates
            points.append(
                {
                    "customer_id": customer_id,
                    "order_id": source.order.id,
                    "latitude": latitude,
                    "longitude": longitude,
                    "order_created_at": source.order.created_at,
                    "order_value": Decimal(source.order.total),
                    "source_channel": (
                        source.order.aggregator_channel or "aggregator"
                        if source.order.source == "aggregator"
                        else source.order.source
                    ),
                }
            )

    # Executemany batches rather than ~13k single-row ORM flushes. Parents
    # first: the membership and point rows reference `customer_cache`.
    for model, rows in (
        (CustomerCache, customers),
        (CustomerOrderCache, memberships),
        (CustomerDeliveryAreaCache, points),
    ):
        if rows:
            await db.execute(insert(model), rows)
