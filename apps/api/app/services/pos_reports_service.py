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

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    Warehouse,
)
from app.models.order import Order, OrderItem
from app.models.payment_method import PaymentMethod
from app.models.pos_order import (
    OrderPayment,
    OrderTax,
    PosOrderStatusEnum,
)
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
                ).join(OrderItem, OrderItem.order_id == Order.id),
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
    Sales grouped by product, category, order type, source, staff, or hour.
    One function because the shape is identical and only the grouping differs.
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

    column = {
        "order_type": Order.order_type,
        "source": Order.source,
        "business_date": Order.business_date,
        "staff": Order.closer_id,
        "hour": func.to_char(Order.closed_at, "HH24"),
    }.get(dimension)
    if column is None:
        raise ValueError(f"Unsupported dimension '{dimension}'")

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
    labels = await _staff_labels(db, rows) if dimension == "staff" else {}
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


async def _staff_labels(db: AsyncSession, rows: Sequence[Any]) -> dict[str, str]:
    ids = {r[0] for r in rows if r[0] is not None}
    if not ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {str(u.id): (u.display_name or u.email) for u in users}


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
