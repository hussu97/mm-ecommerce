"""Build and read the cached cross-channel customer directory.

The cache is invalidated by database triggers on ``users`` and ``orders``.  A
customer read takes the single state row under lock and refreshes only when a
source has changed, keeping customer-list pagination cheap while avoiding a
second mutable customer source of truth.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone import describe_phone
from app.models.customer_cache import (
    CustomerCache,
    CustomerCacheState,
    CustomerOrderCache,
)
from app.models.order import Order, OrderStatusEnum
from app.models.user import User

_SPACE = re.compile(r"\s+")
_CUSTOMER_NAMESPACE = uuid.UUID("71af5e62-7fdd-4ff7-923e-e274328a56d7")


def _clean(value: str | None) -> str | None:
    value = _SPACE.sub(" ", (value or "").strip())
    return value or None


def _name_key(value: str | None) -> str | None:
    return _clean(value.casefold() if value else None)


def _email(value: str | None) -> str | None:
    value = _clean(value)
    # POS orders deliberately carry an empty string to satisfy the legacy
    # non-null web column. A contact without an @ is not an email identity.
    return value.casefold() if value and "@" in value and " " not in value else None


def _order_name(order: Order) -> str | None:
    """Use the delivery snapshot when checkout has no pickup contact."""
    name = _clean(order.customer_name)
    if name:
        return name
    snapshot = order.shipping_address_snapshot or {}
    return _clean(
        " ".join(str(snapshot.get(k) or "") for k in ("first_name", "last_name"))
    )


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


async def refresh_if_dirty(db: AsyncSession) -> None:
    """Rebuild the cache once per source change, atomically within this request."""
    state = await db.scalar(
        select(CustomerCacheState)
        .where(CustomerCacheState.id.is_(True))
        .with_for_update()
    )
    if state is None:
        # The migration seeds this row. Refusing to return a partial directory if
        # an operator has manually damaged it is safer than pretending it is empty.
        raise RuntimeError("customer cache state is missing")
    if not state.dirty:
        return

    orders = (await db.scalars(select(Order))).all()
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
                name=_clean(account.display_name),
                email=_email(account.email),
                phone=describe_phone(account.phone).e164 or _clean(account.phone),
                phone_country=getattr(account, "phone_country", None),
                user_id=account.id,
                observed_at=account.updated_at,
            )
        )

    for order in orders:
        name = _order_name(order)
        email = _email(order.email)
        phone_parts = describe_phone(order.customer_phone)
        phone = phone_parts.e164 or _clean(order.customer_phone)
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

    await db.execute(delete(CustomerOrderCache))
    await db.execute(delete(CustomerCache))

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
        db.add(
            CustomerCache(
                id=customer_id,
                name=_latest_value(component, "name"),
                email=_latest_value(component, "email"),
                phone=_latest_value(component, "phone"),
                phone_country=_latest_value(component, "phone_country"),
                order_count=order_count,
                earliest_order_at=min(dates) if dates else None,
                latest_order_at=max(dates) if dates else None,
                total_revenue=total_revenue,
                aov=(total_revenue / order_count) if order_count else Decimal("0"),
            )
        )
        for source in order_sources:
            db.add(
                CustomerOrderCache(customer_id=customer_id, order_id=source.order.id)
            )

    state.dirty = False
    await db.flush()
