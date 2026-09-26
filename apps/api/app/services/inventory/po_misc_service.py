"""
Misc purchase-order lines: their categories, their period presets, and who may
see a confidential (``admin_only``) category.

Three rules live here rather than in the router:

1. **Every misc line has a category and a period.** The category must be live
   (not deleted, active) unless the line already carried it, so an unrelated
   edit never trips over a since-retired category.
2. **An admin-only category is invisible to anyone who may not see it.** The
   till never sees one, for anyone; the console shows one only to holders of
   ``RESTRICTED_MISC_PERMISSION``. Invisible means as if the line were not on
   the PO: it is dropped from the response, the PO's totals are shown without
   it, and a PO holding nothing else is not found at all.
3. **A preset only pre-fills a range.** ``default_range`` turns ``unit`` ×
   ``length`` into the current block (this month, this quarter, …); a line
   stores its dates, never the preset.
"""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.money import money as _money
from app.models.base import utcnow
from app.models.inventory import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderMiscCategory,
    PurchaseOrderMiscItem,
    PurchaseOrderMiscPeriod,
)
from app.models.user import User

__all__ = [
    "RESTRICTED_MISC_PERMISSION",
    "MAX_PERIOD_DAYS",
    "can_see_gated",
    "assert_can_see_gated",
    "default_range",
    "visible_po_clause",
    "VisibleMisc",
    "visible_misc",
    "hide_gated_lines",
    "po_is_visible",
    "load_categories",
    "list_categories",
    "create_category",
    "update_category",
    "delete_category",
    "restore_category",
    "list_periods",
    "create_period",
    "update_period",
    "delete_period",
]

#: Holders see (and may use) admin-only misc categories in the console.
RESTRICTED_MISC_PERMISSION = "inventory.purchase_orders.restricted_misc"

#: The longest period a misc line may cover — a five-year lease at most.
MAX_PERIOD_DAYS = 5 * 366


# ─── Visibility ───────────────────────────────────────────────────────────────


def can_see_gated(user: User, *, pos: bool = False) -> bool:
    """Whether ``user`` sees admin-only categories. Never on the till."""
    return not pos and user.can(RESTRICTED_MISC_PERMISSION)


def assert_can_see_gated(user: User) -> None:
    if not can_see_gated(user):
        raise NotFoundError("Category not found")


def visible_po_clause():
    """WHERE clause keeping POs a gated-blind viewer may see: any stock line, or
    any misc line whose category is not admin-only. A PO holding only admin-only
    lines (a rent PO) fails it — for that viewer it does not exist."""
    return or_(
        exists().where(PurchaseOrderItem.purchase_order_id == PurchaseOrder.id),
        exists().where(
            PurchaseOrderMiscItem.purchase_order_id == PurchaseOrder.id,
            PurchaseOrderMiscCategory.id == PurchaseOrderMiscItem.category_id,
            PurchaseOrderMiscCategory.admin_only.is_(False),
        ),
        # A PO with no lines at all (not creatable today) stays visible.
        and_(
            not_(
                exists().where(
                    PurchaseOrderMiscItem.purchase_order_id == PurchaseOrder.id
                )
            ),
            not_(
                exists().where(PurchaseOrderItem.purchase_order_id == PurchaseOrder.id)
            ),
        ),
    )


def po_is_visible(purchase_order: PurchaseOrder, *, sees_gated: bool) -> bool:
    """The in-memory twin of ``visible_po_clause`` for one loaded PO."""
    if sees_gated or purchase_order.items or not purchase_order.misc_items:
        return True
    return any(not line.category_admin_only for line in purchase_order.misc_items)


@dataclass(frozen=True)
class VisibleMisc:
    """A PO as one viewer sees it: its misc lines and its totals without the
    admin-only lines they may not see."""

    misc_items: list[PurchaseOrderMiscItem]
    subtotal_net: Decimal
    vat_total: Decimal
    total_gross: Decimal
    total_cost: Decimal


def visible_misc(purchase_order: PurchaseOrder, *, sees_gated: bool) -> VisibleMisc:
    """The misc lines and totals ``purchase_order`` shows a viewer. For a viewer
    blind to admin-only categories those lines drop out and their money comes
    off the totals, so what they see still adds up."""
    lines = list(purchase_order.misc_items)
    hidden = [] if sees_gated else [line for line in lines if line.category_admin_only]
    net = sum((Decimal(str(line.net_total)) for line in hidden), Decimal("0"))
    vat = sum((Decimal(str(line.vat_amount)) for line in hidden), Decimal("0"))
    gross = sum((Decimal(str(line.entered_total)) for line in hidden), Decimal("0"))
    return VisibleMisc(
        misc_items=[line for line in lines if line not in hidden],
        subtotal_net=_money(Decimal(str(purchase_order.subtotal_net or 0)) - net),
        vat_total=_money(Decimal(str(purchase_order.vat_total or 0)) - vat),
        total_gross=_money(Decimal(str(purchase_order.total_gross or 0)) - gross),
        total_cost=_money(Decimal(str(purchase_order.total_cost or 0)) - gross),
    )


def hide_gated_lines(payload, purchase_order: PurchaseOrder) -> None:
    """Apply ``visible_misc`` for a gated-blind viewer to a serialised PO
    (``payload`` is the ``PurchaseOrderResponse`` built from ``purchase_order``)."""
    view = visible_misc(purchase_order, sees_gated=False)
    if len(view.misc_items) == len(purchase_order.misc_items):
        return
    shown = {line.id for line in view.misc_items}
    payload.misc_items = [m for m in payload.misc_items if m.id in shown]
    payload.subtotal_net = view.subtotal_net
    payload.vat_total = view.vat_total
    payload.total_gross = view.total_gross
    payload.total_cost = view.total_cost


# ─── Periods ──────────────────────────────────────────────────────────────────


def _add_months(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def default_range(unit: str, length: int, today: date) -> tuple[date, date]:
    """The block of ``length`` ``unit``s that contains ``today``.

    - ``day``: today and the ``length − 1`` days after it.
    - ``week``: from this week's Monday, ``length`` whole weeks.
    - ``month``: blocks of ``length`` months counted from January, so length 1 is
      this month, 3 this quarter, 6 this half and 12 this calendar year. A length
      that does not divide the year (e.g. 5) simply starts this month.
    """
    length = max(int(length), 1)
    if unit == "day":
        return today, today + timedelta(days=length - 1)
    if unit == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7 * length - 1)
    if unit == "month":
        month = today.month
        if 12 % length == 0:
            month = ((today.month - 1) // length) * length + 1
        start = date(today.year, month, 1)
        end_year, end_month = _add_months(start.year, start.month, length - 1)
        end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
        return start, end
    raise BadRequestError(f"Unknown period unit {unit!r}")


# ─── Line validation ──────────────────────────────────────────────────────────


async def load_categories(
    db: AsyncSession,
    misc_lines: Iterable,
    *,
    allow_gated: bool,
    already_used: Iterable[uuid.UUID] = (),
) -> dict[uuid.UUID, PurchaseOrderMiscCategory]:
    """Resolve and check every misc line's category and period.

    A category must exist; unless this PO already used it, it must also be live
    (not deleted, active). An admin-only one needs ``allow_gated`` — refused with
    the same message as a missing one, so the till learns nothing about it.
    """
    lines = list(misc_lines)
    ids = {line.category_id for line in lines}
    if not ids:
        return {}
    used = set(already_used)
    rows = (
        (
            await db.execute(
                select(PurchaseOrderMiscCategory).where(
                    PurchaseOrderMiscCategory.id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    categories = {row.id: row for row in rows}
    for line in lines:
        category = categories.get(line.category_id)
        if category is None or (category.admin_only and not allow_gated):
            raise BadRequestError(
                f'Pick a category for "{line.name}" — the one chosen is not available'
            )
        if category.id not in used and (
            category.deleted_at is not None or not category.is_active
        ):
            raise BadRequestError(
                f'The category "{category.name}" is no longer in use — pick another '
                f'for "{line.name}"'
            )
        if line.period_to < line.period_from:
            raise BadRequestError(
                f'The period for "{line.name}" must end on or after it starts'
            )
        if (line.period_to - line.period_from).days + 1 > MAX_PERIOD_DAYS:
            raise BadRequestError(
                f'The period for "{line.name}" is longer than five years'
            )
    return categories


# ─── Category CRUD ────────────────────────────────────────────────────────────


async def list_categories(
    db: AsyncSession,
    *,
    include_gated: bool,
    include_inactive: bool = True,
    include_deleted: bool = False,
) -> list[PurchaseOrderMiscCategory]:
    """Categories in alphabetical order (the order every picker shows)."""
    stmt = select(PurchaseOrderMiscCategory).order_by(
        func.lower(PurchaseOrderMiscCategory.name)
    )
    if not include_gated:
        stmt = stmt.where(PurchaseOrderMiscCategory.admin_only.is_(False))
    if not include_inactive:
        stmt = stmt.where(PurchaseOrderMiscCategory.is_active.is_(True))
    if not include_deleted:
        stmt = stmt.where(PurchaseOrderMiscCategory.deleted_at.is_(None))
    return list((await db.execute(stmt)).scalars().all())


async def _assert_category_name_free(
    db: AsyncSession, name: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(PurchaseOrderMiscCategory.id).where(
        func.lower(PurchaseOrderMiscCategory.name) == name.lower(),
        PurchaseOrderMiscCategory.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(PurchaseOrderMiscCategory.id != exclude_id)
    if (await db.execute(stmt)).first() is not None:
        raise ConflictError(f'A category named "{name}" already exists')


async def _get_category(
    db: AsyncSession, category_id: uuid.UUID, user: User
) -> PurchaseOrderMiscCategory:
    category = await db.get(PurchaseOrderMiscCategory, category_id)
    if category is None or (category.admin_only and not can_see_gated(user)):
        raise NotFoundError("Category not found")
    return category


async def create_category(
    db: AsyncSession, user: User, data
) -> PurchaseOrderMiscCategory:
    if data.admin_only:
        assert_can_see_gated(user)
    name = " ".join(data.name.split())
    await _assert_category_name_free(db, name)
    category = PurchaseOrderMiscCategory(
        name=name, admin_only=data.admin_only, is_active=data.is_active
    )
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return category


async def update_category(
    db: AsyncSession, user: User, category_id: uuid.UUID, data
) -> PurchaseOrderMiscCategory:
    category = await _get_category(db, category_id, user)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("admin_only"):
        assert_can_see_gated(user)
    if changes.get("name") is not None:
        changes["name"] = " ".join(changes["name"].split())
        await _assert_category_name_free(db, changes["name"], exclude_id=category.id)
    for key, value in changes.items():
        if value is not None:
            setattr(category, key, value)
    await db.flush()
    await db.refresh(category)
    return category


async def delete_category(
    db: AsyncSession, user: User, category_id: uuid.UUID
) -> PurchaseOrderMiscCategory:
    """Soft delete: gone from every picker; lines that use it keep it."""
    category = await _get_category(db, category_id, user)
    category.deleted_at = utcnow()
    await db.flush()
    await db.refresh(category)
    return category


async def restore_category(
    db: AsyncSession, user: User, category_id: uuid.UUID
) -> PurchaseOrderMiscCategory:
    category = await _get_category(db, category_id, user)
    if category.deleted_at is not None:
        await _assert_category_name_free(db, category.name, exclude_id=category.id)
        category.deleted_at = None
        await db.flush()
        await db.refresh(category)
    return category


# ─── Period-preset CRUD ───────────────────────────────────────────────────────


async def list_periods(db: AsyncSession) -> list[PurchaseOrderMiscPeriod]:
    stmt = (
        select(PurchaseOrderMiscPeriod)
        .where(PurchaseOrderMiscPeriod.deleted_at.is_(None))
        .order_by(
            PurchaseOrderMiscPeriod.display_order,
            func.lower(PurchaseOrderMiscPeriod.name),
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def _assert_period_name_free(
    db: AsyncSession, name: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(PurchaseOrderMiscPeriod.id).where(
        func.lower(PurchaseOrderMiscPeriod.name) == name.lower(),
        PurchaseOrderMiscPeriod.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(PurchaseOrderMiscPeriod.id != exclude_id)
    if (await db.execute(stmt)).first() is not None:
        raise ConflictError(f'A period named "{name}" already exists')


async def _clear_default(db: AsyncSession, *, keep: uuid.UUID | None = None) -> None:
    """At most one default preset: making one the default demotes the rest."""
    stmt = update(PurchaseOrderMiscPeriod).where(
        PurchaseOrderMiscPeriod.is_default.is_(True)
    )
    if keep is not None:
        stmt = stmt.where(PurchaseOrderMiscPeriod.id != keep)
    await db.execute(stmt.values(is_default=False))


async def _get_period(
    db: AsyncSession, period_id: uuid.UUID
) -> PurchaseOrderMiscPeriod:
    period = await db.get(PurchaseOrderMiscPeriod, period_id)
    if period is None or period.deleted_at is not None:
        raise NotFoundError("Period not found")
    return period


async def create_period(db: AsyncSession, data) -> PurchaseOrderMiscPeriod:
    name = " ".join(data.name.split())
    await _assert_period_name_free(db, name)
    if data.is_default:
        await _clear_default(db)
    period = PurchaseOrderMiscPeriod(
        name=name,
        unit=data.unit,
        length=data.length,
        is_default=data.is_default,
        display_order=data.display_order,
    )
    db.add(period)
    await db.flush()
    await db.refresh(period)
    return period


async def update_period(
    db: AsyncSession, period_id: uuid.UUID, data
) -> PurchaseOrderMiscPeriod:
    period = await _get_period(db, period_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        changes["name"] = " ".join(changes["name"].split())
        await _assert_period_name_free(db, changes["name"], exclude_id=period.id)
    if changes.get("is_default"):
        await _clear_default(db, keep=period.id)
    for key, value in changes.items():
        if value is not None:
            setattr(period, key, value)
    await db.flush()
    await db.refresh(period)
    return period


async def delete_period(
    db: AsyncSession, period_id: uuid.UUID
) -> PurchaseOrderMiscPeriod:
    period = await _get_period(db, period_id)
    period.deleted_at = utcnow()
    period.is_default = False
    await db.flush()
    return period
