"""
POS reporting.

Every report is scoped by `business_date` rather than by `created_at`, so a
trading day that runs past midnight reports as one day. Only closed orders count
toward sales; open checks are work in progress, and voided ones are reported
separately rather than netted silently into the totals.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import Numeric, Select, func, select
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    Supplier,
    Warehouse,
)
from app.models.order import Order, OrderItem
from app.models.payment_method import PaymentMethod
from app.models.branch import Branch
from app.models.pos_order import (
    KitchenTicket,
    OrderCharge,
    OrderDiscount,
    OrderPayment,
    OrderTax,
    PosOrderStatusEnum,
)
from app.models.pos_table import PosTable, Section
from app.models.tag import Tag, TaggedEntity
from app.models.product import Product
from app.models.till import DrawerOperation, Till
from app.models.user import User

ZERO = Decimal("0.00")


def _q(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _scope(
    stmt: Select[Any],
    *,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
) -> Select[Any]:
    """Apply the standard branch + business-date window to an order query."""
    stmt = stmt.where(Order.is_pos.is_(True))
    if branch_id:
        stmt = stmt.where(Order.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Order.business_date >= date_from)
    if date_to:
        stmt = stmt.where(Order.business_date <= date_to)
    return stmt


CLOSED = PosOrderStatusEnum.CLOSED.value
VOID = PosOrderStatusEnum.VOID.value


# ─── Sales ────────────────────────────────────────────────────────────────────


async def sales_summary(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Headline trading figures for the window."""
    stmt = _scope(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.subtotal), 0),
            func.coalesce(func.sum(Order.discount_amount), 0),
            func.coalesce(func.sum(Order.charges_amount), 0),
            func.coalesce(func.sum(Order.vat_amount), 0),
            func.coalesce(func.sum(Order.total_excl_vat), 0),
            func.coalesce(func.sum(Order.rounding_amount), 0),
            func.coalesce(func.sum(Order.tips_amount), 0),
            func.coalesce(func.sum(Order.total), 0),
        ),
        branch_id=branch_id,
        date_from=date_from,
        date_to=date_to,
    ).where(Order.pos_status == CLOSED)

    row = (await db.execute(stmt)).one()
    (
        orders,
        subtotal,
        discounts,
        charges,
        vat,
        net_excl,
        rounding,
        tips,
        total,
    ) = row

    voided = (
        await db.execute(
            _scope(
                select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            ).where(Order.pos_status == VOID)
        )
    ).one()

    returns = (
        await db.execute(
            _scope(
                select(
                    func.coalesce(
                        func.sum(OrderItem.returned_quantity * OrderItem.unit_price), 0
                    )
                )
                # The select list only mentions OrderItem, so the left side of the
                # join has to be stated explicitly or SQLAlchemy cannot infer it.
                .select_from(Order)
                .join(OrderItem, OrderItem.order_id == Order.id),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
        )
    ).scalar_one()

    order_count = int(orders or 0)
    net_sales = _q(total)
    return {
        "orders_count": order_count,
        "gross_sales": _q(subtotal),
        "discounts": _q(discounts),
        "charges": _q(charges),
        "returns": _q(returns),
        "taxes": _q(vat),
        "net_sales_excl_tax": _q(net_excl),
        "rounding": _q(rounding),
        "tips": _q(tips),
        "net_sales": net_sales,
        "average_order_value": _q(net_sales / order_count) if order_count else ZERO,
        "voided_orders": int(voided[0] or 0),
        "voided_value": _q(voided[1]),
    }


async def sales_by_dimension(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Sales grouped by any of the dimensions a GM actually asks about.

    One function rather than a route per dimension: the query shape is
    identical and only the grouping column changes. Foodics ships this as
    twenty-odd separate `sales-by-*` screens; the same answers come out of
    one endpoint with a `dimension` parameter.
    """
    if dimension in {"product", "category"}:
        return await _sales_by_item(
            db,
            group_by_category=(dimension == "category"),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    if dimension == "modifier_option":
        return await _sales_by_modifier_option(
            db, branch_id=branch_id, date_from=date_from, date_to=date_to, limit=limit
        )

    if dimension in _TABLE_DIMENSIONS:
        return await _sales_by_seating(
            db,
            dimension=dimension,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    if dimension in _ENTITY_TAG_DIMENSIONS:
        return await _sales_by_tag(
            db,
            dimension=dimension,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    if dimension in _DISCOUNT_SOURCES or dimension in _LINE_DIMENSIONS:
        return await _sales_by_related(
            db,
            dimension=dimension,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    column = _ORDER_DIMENSIONS.get(dimension)
    if column is None:
        raise ValueError(
            f"Unsupported dimension '{dimension}'. "
            f"Try one of: {', '.join(sorted(SUPPORTED_DIMENSIONS))}"
        )

    stmt = (
        _scope(
            select(
                column.label("key"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
                func.coalesce(func.sum(Order.discount_amount), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .where(Order.pos_status == CLOSED)
        .group_by(column)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    labels = await _labels_for(db, dimension, rows)
    return [
        {
            "key": str(key) if key is not None else "unknown",
            "label": labels.get(str(key), str(key) if key is not None else "Unknown"),
            "orders": int(count or 0),
            "net_sales": _q(total),
            "discounts": _q(discount),
        }
        for key, count, total, discount in rows
    ]


#: Dimensions that group the order rows themselves.
_ORDER_DIMENSIONS = {
    "order_type": Order.order_type,
    "source": Order.source,
    "business_date": Order.business_date,
    # Foodics separates "cashier" (who closed it) from "creator" (who rang it
    # up); on a single-terminal shift they are the same person, on a busy one
    # they are not, and the split is how a manager spots a hand-off.
    "staff": Order.closer_id,
    "cashier": Order.closer_id,
    "creator": Order.creator_id,
    "driver": Order.driver_id,
    "customer": Order.user_id,
    "branch": Order.branch_id,
    "table": Order.table_id,
    "hour": func.to_char(Order.closed_at, "HH24"),
}

#: Dimensions that live on a child row, so they need a join and a sum of the
#: child's own amount rather than the order total — a check with two discounts
#: must not count its full value against each of them.
_LINE_DIMENSIONS = {"discount", "charge", "tax"}

#: Discounts carry where they came from, so coupon, promotion and timed-event
#: are the same grouping narrowed to one source.
_DISCOUNT_SOURCES = {
    "coupon": "coupon",
    "promotion": "promotion",
    "timed_event": "timed_event",
}

#: Dimensions reached through the table an order was seated at.
_TABLE_DIMENSIONS = {"section", "revenue_center"}

#: Tags attached to something other than the order or the product.
_ENTITY_TAG_DIMENSIONS = {
    "branch_tag": "branch",
    "product_tag": "product",
    "order_tag": "order",
}

SUPPORTED_DIMENSIONS = (
    set(_ORDER_DIMENSIONS)
    | _LINE_DIMENSIONS
    | set(_DISCOUNT_SOURCES)
    | _TABLE_DIMENSIONS
    | set(_ENTITY_TAG_DIMENSIONS)
    | {"product", "category", "modifier_option"}
)


async def _staff_labels(db: AsyncSession, rows: Sequence[Any]) -> dict[str, str]:
    """Display names for grouped user ids, keyed by id string."""
    ids = {r[0] for r in rows if r[0] is not None}
    if not ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {str(u.id): (u.display_name or u.email) for u in users}


async def _labels_for(
    db: AsyncSession, dimension: str, rows: Sequence[Any]
) -> dict[str, str]:
    """Turn grouped foreign keys into names a human recognises."""
    ids = {r[0] for r in rows if r[0] is not None}
    if not ids:
        return {}

    if dimension in {"staff", "cashier", "creator", "driver", "customer"}:
        return await _staff_labels(db, rows)

    if dimension == "branch":
        branches = (
            (await db.execute(select(Branch).where(Branch.id.in_(ids)))).scalars().all()
        )
        return {str(b.id): b.name for b in branches}

    if dimension == "table":
        tables = (
            (await db.execute(select(PosTable).where(PosTable.id.in_(ids))))
            .scalars()
            .all()
        )
        return {str(t.id): t.name for t in tables}

    return {}


async def _sales_by_related(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Group by something attached to the order rather than on it.

    The amount summed is the child's own — the discount given, the charge
    levied — not the order total, so two discounts on one check do not each
    claim the whole check's value.
    """
    model, amount = {
        "discount": (OrderDiscount, OrderDiscount.amount),
        "charge": (OrderCharge, OrderCharge.amount),
        "tax": (OrderTax, OrderTax.amount),
    }.get(dimension, (OrderDiscount, OrderDiscount.amount))

    stmt = (
        _scope(
            select(
                model.name.label("key"),
                func.count(func.distinct(Order.id)),
                func.coalesce(func.sum(amount), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(model, model.order_id == Order.id)
        .where(Order.pos_status == CLOSED)
        .where(
            OrderDiscount.source == _DISCOUNT_SOURCES[dimension]
            if dimension in _DISCOUNT_SOURCES
            else sa_true()
        )
        .group_by(model.name)
        .order_by(func.coalesce(func.sum(amount), 0).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": name or "unknown",
            "label": name or "Unknown",
            "orders": int(count or 0),
            # For these dimensions the money *is* the discount or charge, so
            # it is reported under both keys rather than inventing a new one
            # the admin table would have to special-case.
            "net_sales": _q(total),
            "discounts": _q(total) if dimension == "discount" else _q(0),
        }
        for name, count, total in rows
    ]


async def _sales_by_item(
    db: AsyncSession,
    *,
    group_by_category: bool,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    quantity = func.sum(OrderItem.quantity - OrderItem.returned_quantity)
    revenue = func.sum(OrderItem.total_price)

    if group_by_category:
        key, label = Category.id, Category.name
        stmt = (
            select(key, label, quantity, revenue, func.sum(OrderItem.discount_amount))
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(Category, Category.id == Product.category_id)
        )
    else:
        key, label = OrderItem.product_id, OrderItem.product_name
        stmt = (
            select(key, label, quantity, revenue, func.sum(OrderItem.discount_amount))
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
        )

    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)
    stmt = (
        stmt.where(Order.pos_status == CLOSED, OrderItem.status != "void")
        .group_by(key, label)
        .order_by(revenue.desc())
        .limit(limit)
    )

    return [
        {
            "key": str(k) if k is not None else "unknown",
            "label": name or "Unknown",
            "quantity": int(qty or 0),
            "net_sales": _q(total),
            "discounts": _q(discount),
        }
        for k, name, qty, total, discount in (await db.execute(stmt)).all()
    ]


async def payments_report(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Tender mix — what customers actually paid with."""
    stmt = (
        select(
            PaymentMethod.id,
            PaymentMethod.name,
            PaymentMethod.type,
            func.count(OrderPayment.id),
            func.coalesce(func.sum(OrderPayment.amount), 0),
            func.coalesce(func.sum(OrderPayment.tips), 0),
        )
        .select_from(OrderPayment)
        .join(Order, Order.id == OrderPayment.order_id)
        .join(PaymentMethod, PaymentMethod.id == OrderPayment.payment_method_id)
    )
    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)
    stmt = stmt.where(OrderPayment.is_refund.is_(False)).group_by(
        PaymentMethod.id, PaymentMethod.name, PaymentMethod.type
    )

    refunds_stmt = (
        select(
            OrderPayment.payment_method_id,
            func.coalesce(func.sum(OrderPayment.amount), 0),
        )
        .select_from(OrderPayment)
        .join(Order, Order.id == OrderPayment.order_id)
    )
    refunds_stmt = (
        _scope(refunds_stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)
        .where(OrderPayment.is_refund.is_(True))
        .group_by(OrderPayment.payment_method_id)
    )
    refunds = {str(k): _q(v) for k, v in (await db.execute(refunds_stmt)).all()}

    return [
        {
            "payment_method_id": str(pid),
            "name": name,
            "type": ptype,
            "transactions": int(count or 0),
            "amount": _q(amount),
            "refunds": refunds.get(str(pid), ZERO),
            "net": _q(Decimal(str(amount or 0)) - refunds.get(str(pid), ZERO)),
            "tips": _q(tips),
        }
        for pid, name, ptype, count, amount, tips in (await db.execute(stmt)).all()
    ]


async def tax_report(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """VAT return input: taxable base and tax collected, per rate."""
    stmt = (
        select(
            OrderTax.name,
            OrderTax.rate,
            func.coalesce(func.sum(OrderTax.taxable_amount), 0),
            func.coalesce(func.sum(OrderTax.amount), 0),
        )
        .select_from(OrderTax)
        .join(Order, Order.id == OrderTax.order_id)
    )
    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)
    stmt = (
        stmt.where(Order.pos_status == CLOSED)
        .group_by(OrderTax.name, OrderTax.rate)
        .order_by(OrderTax.rate)
    )
    return [
        {
            "name": name,
            "rate": float(rate or 0),
            "rate_percent": round(float(rate or 0) * 100, 2),
            "taxable_amount": _q(base),
            "tax_amount": _q(amount),
        }
        for name, rate, base, amount in (await db.execute(stmt)).all()
    ]


async def voids_and_returns(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Loss-prevention view: who voided or returned what, and why. Deliberately
    itemised rather than aggregated — the value is in the individual events.
    """
    stmt = (
        select(
            Order.order_number,
            Order.business_date,
            OrderItem.product_name,
            OrderItem.quantity,
            OrderItem.returned_quantity,
            OrderItem.unit_price,
            OrderItem.status,
            OrderItem.voided_at,
            OrderItem.voided_by_id,
            OrderItem.void_reason_id,
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
    )
    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)
    stmt = (
        stmt.where(
            (OrderItem.status.in_(["void", "returned"]))
            | (OrderItem.returned_quantity > 0)
        )
        .order_by(OrderItem.voided_at.desc().nullslast())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    labels = await _staff_labels(db, [(r.voided_by_id,) for r in rows])
    return [
        {
            "order_number": r.order_number,
            "business_date": r.business_date,
            "product_name": r.product_name,
            "quantity": r.quantity,
            "returned_quantity": r.returned_quantity,
            "value": _q(
                Decimal(str(r.unit_price)) * (r.returned_quantity or r.quantity or 0)
            ),
            "status": r.status,
            "voided_at": r.voided_at,
            "staff": labels.get(str(r.voided_by_id), None),
            "reason_id": str(r.void_reason_id) if r.void_reason_id else None,
        }
        for r in rows
    ]


# ─── Operations ───────────────────────────────────────────────────────────────


async def tills_report(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    stmt = select(Till)
    if branch_id:
        stmt = stmt.where(Till.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Till.business_date >= date_from)
    if date_to:
        stmt = stmt.where(Till.business_date <= date_to)
    stmt = stmt.order_by(Till.opened_at.desc())

    tills = list((await db.execute(stmt)).scalars().all())
    labels = await _staff_labels(db, [(t.user_id,) for t in tills])
    return [
        {
            "till_id": str(t.id),
            "business_date": t.business_date,
            "status": t.status,
            "user": labels.get(str(t.user_id), "Unknown"),
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
            "opening_amount": _q(t.opening_amount),
            "estimated_cash": _q(t.estimated_cash),
            "closing_amount": _q(t.closing_amount)
            if t.closing_amount is not None
            else None,
            "variance": _q(t.variance),
            "totals": t.totals or {},
        }
        for t in tills
    ]


async def drawer_operations_report(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    stmt = (
        select(
            DrawerOperation.type,
            func.count(DrawerOperation.id),
            func.coalesce(func.sum(DrawerOperation.amount), 0),
        )
        .select_from(DrawerOperation)
        .join(Till, Till.id == DrawerOperation.till_id)
    )
    if branch_id:
        stmt = stmt.where(Till.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Till.business_date >= date_from)
    if date_to:
        stmt = stmt.where(Till.business_date <= date_to)
    stmt = stmt.group_by(DrawerOperation.type)

    return [
        {"type": op_type, "count": int(count or 0), "amount": _q(amount)}
        for op_type, count, amount in (await db.execute(stmt)).all()
    ]


# ─── Inventory ────────────────────────────────────────────────────────────────


async def inventory_valuation(
    db: AsyncSession, *, branch_id: uuid.UUID | None = None
) -> dict:
    """Total value of stock on hand, and the items below their reorder point."""
    stmt = (
        select(InventoryLevel, InventoryItem)
        .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
        .where(InventoryItem.deleted_at.is_(None))
    )
    if branch_id:
        stmt = stmt.join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id).where(
            Warehouse.branch_id == branch_id
        )

    total_value = ZERO
    below: list[dict] = []
    item_count = 0
    for level, item in (await db.execute(stmt)).all():
        item_count += 1
        total_value += Decimal(str(level.quantity)) * Decimal(str(level.average_cost))
        if Decimal(str(level.quantity)) < Decimal(str(item.minimum_level)):
            below.append(
                {
                    "item_id": str(item.id),
                    "sku": item.sku,
                    "name": item.name,
                    "quantity": _q(level.quantity),
                    "minimum_level": _q(item.minimum_level),
                    "par_level": _q(item.par_level),
                    "unit": item.ingredient_unit,
                    "shortfall": _q(
                        Decimal(str(item.par_level)) - Decimal(str(level.quantity))
                    ),
                }
            )

    return {
        "items_tracked": item_count,
        "total_value": _q(total_value),
        "below_minimum_count": len(below),
        "below_minimum": below,
    }


async def cost_of_goods(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """
    COGS from the depletion ledger, and the resulting margin against net sales.
    Sourced from posted consumption transactions rather than from recipes, so it
    reflects what was actually taken out of stock.
    """
    stmt = (
        select(func.coalesce(func.sum(InventoryTransactionItem.total_cost), 0))
        .select_from(InventoryTransactionItem)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryTransactionItem.transaction_id,
        )
        .where(
            InventoryTransaction.type
            == InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
            InventoryTransaction.status == "closed",
        )
    )
    if branch_id:
        stmt = stmt.where(InventoryTransaction.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(InventoryTransaction.business_date >= date_from)
    if date_to:
        stmt = stmt.where(InventoryTransaction.business_date <= date_to)

    cogs = _q((await db.execute(stmt)).scalar_one())
    sales = await sales_summary(
        db, branch_id=branch_id, date_from=date_from, date_to=date_to
    )
    net = sales["net_sales_excl_tax"]
    margin = _q(net - cogs)
    return {
        "cost_of_goods": cogs,
        "net_sales_excl_tax": net,
        "gross_margin": margin,
        "gross_margin_percent": (round(float(margin / net) * 100, 2) if net else 0.0),
    }


async def menu_engineering(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Classify menu items by popularity and margin — the classic
    star / plough-horse / puzzle / dog quadrants.
    """
    rows = await _sales_by_item(
        db,
        group_by_category=False,
        branch_id=branch_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    if not rows:
        return []

    total_quantity = sum(r["quantity"] for r in rows)
    average_quantity = total_quantity / len(rows) if rows else 0

    enriched: list[dict] = []
    for row in rows:
        product = (
            await db.get(Product, uuid.UUID(row["key"]))
            if row["key"] != "unknown"
            else None
        )
        cost = Decimal(str(product.cost)) if product and product.cost else ZERO
        revenue = row["net_sales"]
        item_cost = _q(cost * row["quantity"])
        margin = _q(revenue - item_cost)
        margin_percent = float(margin / revenue) if revenue else 0.0
        enriched.append(
            {
                **row,
                "cost": item_cost,
                "margin": margin,
                "margin_percent": margin_percent,
            }
        )

    average_margin = (
        sum(r["margin_percent"] for r in enriched) / len(enriched) if enriched else 0
    )

    for row in enriched:
        popular = row["quantity"] >= average_quantity
        profitable = row["margin_percent"] >= average_margin
        row["classification"] = (
            "star"
            if popular and profitable
            else "plough_horse"
            if popular
            else "puzzle"
            if profitable
            else "dog"
        )
    return enriched


async def speed_of_service(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """
    How long the kitchen and the counter actually take.

    Three spans, each measured only over the tickets that reached that stage,
    so a ticket still cooking does not drag the "prep" average down:

      * acknowledge — sent to the kitchen until someone started it
      * prep        — started until marked ready
      * total       — sent until ready, the number a customer feels

    Averages alone hide the bad days, so the slowest ticket is reported too.
    """
    started = KitchenTicket.started_at.isnot(None)
    completed = KitchenTicket.completed_at.isnot(None)

    def seconds(start, end):
        return func.extract("epoch", end - start)

    stmt = (
        select(
            func.count(KitchenTicket.id),
            func.count(KitchenTicket.id).filter(started),
            func.count(KitchenTicket.id).filter(completed),
            func.avg(seconds(KitchenTicket.sent_at, KitchenTicket.started_at)).filter(
                started
            ),
            func.avg(
                seconds(KitchenTicket.started_at, KitchenTicket.completed_at)
            ).filter(completed & started),
            func.avg(seconds(KitchenTicket.sent_at, KitchenTicket.completed_at)).filter(
                completed
            ),
            func.max(seconds(KitchenTicket.sent_at, KitchenTicket.completed_at)).filter(
                completed
            ),
        )
        .select_from(KitchenTicket)
        .join(Order, Order.id == KitchenTicket.order_id)
        .where(KitchenTicket.sent_at.isnot(None))
    )
    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)

    (
        tickets,
        acknowledged,
        finished,
        avg_ack,
        avg_prep,
        avg_total,
        slowest,
    ) = (await db.execute(stmt)).one()

    def minutes(value) -> Decimal:
        return _q(Decimal(str(value or 0)) / Decimal(60))

    return {
        "tickets": int(tickets or 0),
        "acknowledged": int(acknowledged or 0),
        "completed": int(finished or 0),
        # Still open at the end of the window — the queue, not a delay.
        "outstanding": int((tickets or 0) - (finished or 0)),
        "avg_acknowledge_minutes": minutes(avg_ack),
        "avg_prep_minutes": minutes(avg_prep),
        "avg_total_minutes": minutes(avg_total),
        "slowest_ticket_minutes": minutes(slowest),
    }


async def _sales_by_modifier_option(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Which modifier options actually sell — oat milk versus full fat.

    The chosen options are a JSON snapshot on the line rather than rows, so
    they are expanded here. The snapshot is deliberate: it records what the
    customer was charged at the time, and must not change when someone later
    edits the modifier's price.
    """
    option = func.jsonb_array_elements(
        func.cast(OrderItem.selected_options_snapshot, JSONB)
    ).alias("option")
    name = func.coalesce(option.column.op("->>")("name"), "Unknown")
    price = func.coalesce(
        func.cast(func.nullif(option.column.op("->>")("price"), ""), Numeric), 0
    )
    quantity = OrderItem.quantity - OrderItem.returned_quantity

    stmt = (
        _scope(
            select(
                name.label("key"),
                func.sum(quantity),
                func.coalesce(func.sum(price * quantity), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(option, sa_true())
        .where(Order.pos_status == CLOSED)
        .group_by(name)
        .order_by(func.sum(quantity).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": key,
            "label": key,
            "quantity": int(qty or 0),
            "orders": int(qty or 0),
            "net_sales": _q(total),
            "discounts": _q(0),
        }
        for key, qty, total in rows
    ]


async def _sales_by_seating(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """Sales by the section, or revenue centre, an order was seated in."""
    if dimension == "section":
        key, label = Section.id, Section.name
        joined = (
            select()
            .join(PosTable, PosTable.id == Order.table_id)
            .join(Section, Section.id == PosTable.section_id)
        )
    else:
        # Foodics models a revenue centre as a tag on the table, and so do we.
        key, label = Tag.id, Tag.name
        joined = (
            select()
            .join(PosTable, PosTable.id == Order.table_id)
            .join(Tag, Tag.id == PosTable.revenue_center_tag_id)
        )

    stmt = (
        _scope(
            select(
                label.label("key"),
                func.count(func.distinct(Order.id)),
                func.coalesce(func.sum(Order.total), 0),
                func.coalesce(func.sum(Order.discount_amount), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(PosTable, PosTable.id == Order.table_id)
        .join(
            Section if dimension == "section" else Tag,
            (Section.id == PosTable.section_id)
            if dimension == "section"
            else (Tag.id == PosTable.revenue_center_tag_id),
        )
        .where(Order.pos_status == CLOSED)
        .group_by(label)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )
    del key, joined
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": k or "unknown",
            "label": k or "Unknown",
            "orders": int(c or 0),
            "net_sales": _q(t),
            "discounts": _q(d),
        }
        for k, c, t, d in rows
    ]


async def _sales_by_tag(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Sales grouped by a tag on the product, or on the order itself.

    Product tags sum the lines carrying them; order tags sum whole orders. A
    product tag must not claim the whole check — a "vegan" tag on one slice
    says nothing about the coffee next to it.
    """
    if dimension == "branch_tag":
        stmt = (
            _scope(
                select(
                    Tag.name.label("key"),
                    func.count(func.distinct(Order.id)),
                    func.coalesce(func.sum(Order.total), 0),
                ),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            .select_from(Order)
            .join(
                TaggedEntity,
                (TaggedEntity.entity_id == Order.branch_id)
                & (TaggedEntity.entity_type == "branch"),
            )
            .join(Tag, Tag.id == TaggedEntity.tag_id)
        )
    elif dimension == "product_tag":
        stmt = (
            _scope(
                select(
                    Tag.name.label("key"),
                    func.count(func.distinct(Order.id)),
                    func.coalesce(func.sum(OrderItem.total_price), 0),
                ),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            .select_from(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(
                TaggedEntity,
                (TaggedEntity.entity_id == OrderItem.product_id)
                & (TaggedEntity.entity_type == "product"),
            )
            .join(Tag, Tag.id == TaggedEntity.tag_id)
        )
    else:
        stmt = (
            _scope(
                select(
                    Tag.name.label("key"),
                    func.count(func.distinct(Order.id)),
                    func.coalesce(func.sum(Order.total), 0),
                ),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            .select_from(Order)
            .join(
                TaggedEntity,
                (TaggedEntity.entity_id == Order.id)
                & (TaggedEntity.entity_type == "order"),
            )
            .join(Tag, Tag.id == TaggedEntity.tag_id)
        )

    stmt = (
        stmt.where(Order.pos_status == CLOSED)
        .group_by(Tag.name)
        .order_by(func.count(func.distinct(Order.id)).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": k,
            "label": k,
            "orders": int(c or 0),
            "net_sales": _q(t),
            "discounts": _q(0),
        }
        for k, c, t in rows
    ]


async def branches_trend(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    Each branch's sales per business day, for comparing sites over time.

    Returned long rather than pivoted: the caller decides whether to draw one
    line per branch or a table, and a pivot would have to guess the date range
    that happens to have data.
    """
    stmt = (
        _scope(
            select(
                Branch.name,
                Order.business_date,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(Branch, Branch.id == Order.branch_id)
        .where(Order.pos_status == CLOSED)
        .group_by(Branch.name, Order.business_date)
        .order_by(Order.business_date.asc(), Branch.name.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "branch": name,
            "business_date": day,
            "orders": int(count or 0),
            "net_sales": _q(total),
            "average_order_value": _q(Decimal(str(total or 0)) / count)
            if count
            else ZERO,
        }
        for name, day, count, total in rows
    ]


async def table_utilization(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    How hard each table works: covers, turns and how long a party sits.

    Only dine-in checks count. A takeaway order has no table, and counting it
    would flatter every number here.
    """
    minutes = func.extract("epoch", Order.closed_at - Order.opened_at) / 60

    stmt = (
        _scope(
            select(
                Section.name,
                PosTable.name,
                PosTable.seats,
                func.count(Order.id),
                func.coalesce(func.sum(Order.guests), 0),
                func.coalesce(func.sum(Order.total), 0),
                func.avg(minutes),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(PosTable, PosTable.id == Order.table_id)
        .outerjoin(Section, Section.id == PosTable.section_id)
        .where(Order.pos_status == CLOSED, Order.closed_at.isnot(None))
        .group_by(Section.name, PosTable.name, PosTable.seats)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "section": section or "Unassigned",
            "table": table,
            "seats": int(seats or 0),
            # "Turns" is covers per seat: how many parties that seat served.
            "turns": int(turns or 0),
            "covers": int(covers or 0),
            "net_sales": _q(total),
            "average_minutes": _q(Decimal(str(avg_minutes or 0))),
            "sales_per_seat": _q(Decimal(str(total or 0)) / seats) if seats else ZERO,
        }
        for section, table, seats, turns, covers, total, avg_minutes in rows
    ]


async def suppliers_analysis(
    db: AsyncSession,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """What each supplier has been paid, and how much was ordered from them."""
    stmt = (
        select(
            Supplier.name,
            func.count(func.distinct(PurchaseOrder.id)),
            func.coalesce(func.sum(PurchaseOrder.total_cost), 0),
        )
        .select_from(PurchaseOrder)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .group_by(Supplier.name)
        .order_by(func.coalesce(func.sum(PurchaseOrder.total_cost), 0).desc())
        .limit(limit)
    )
    if date_from:
        stmt = stmt.where(PurchaseOrder.business_date >= date_from)
    if date_to:
        stmt = stmt.where(PurchaseOrder.business_date <= date_to)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "supplier": name,
            "purchase_orders": int(count or 0),
            "total_spend": _q(total),
        }
        for name, count, total in rows
    ]


async def cost_adjustment_history(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Every stock revaluation, newest first.

    Cost adjustments move money without moving stock, so they never show up in
    a quantity report — which is exactly why they need their own trail.
    """
    stmt = (
        select(
            InventoryTransaction.reference,
            InventoryTransaction.business_date,
            InventoryTransaction.notes,
            InventoryItem.name,
            InventoryItem.sku,
            InventoryTransactionItem.quantity,
            InventoryTransactionItem.unit_cost,
            InventoryTransactionItem.total_cost,
        )
        .select_from(InventoryTransaction)
        .join(
            InventoryTransactionItem,
            InventoryTransactionItem.transaction_id == InventoryTransaction.id,
        )
        .join(InventoryItem, InventoryItem.id == InventoryTransactionItem.item_id)
        .where(
            InventoryTransaction.type.in_(
                [
                    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value,
                    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value,
                    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
                    InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
                ]
            )
        )
        .order_by(InventoryTransaction.created_at.desc())
        .limit(limit)
    )
    if branch_id:
        stmt = stmt.where(InventoryTransaction.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(InventoryTransaction.business_date >= date_from)
    if date_to:
        stmt = stmt.where(InventoryTransaction.business_date <= date_to)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "reference": ref,
            "business_date": day,
            "item": name,
            "sku": sku,
            "quantity": _q(qty),
            "unit_cost": _q(unit),
            "total_cost": _q(total),
            "notes": notes,
        }
        for ref, day, notes, name, sku, qty, unit, total in rows
    ]
