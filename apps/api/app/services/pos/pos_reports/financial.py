"""Payments, tax, voids and returns, tills and the drawer."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.device import Device
from app.models.order import Order, OrderItem
from app.models.payment_method import PaymentMethod
from app.models.pos_order import (
    OrderPayment,
    OrderTax,
)
from app.models.till import DrawerOperation, Till

from ._base import (
    CLOSED,
    ZERO,
    _scope,
    _staff_labels,
)


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
    refunds = {str(k): money(v) for k, v in (await db.execute(refunds_stmt)).all()}

    return [
        {
            "payment_method_id": str(pid),
            "name": name,
            "type": ptype,
            "transactions": int(count or 0),
            "amount": money(amount),
            "refunds": refunds.get(str(pid), ZERO),
            "net": money(Decimal(str(amount or 0)) - refunds.get(str(pid), ZERO)),
            "tips": money(tips),
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
            "taxable_amount": money(base),
            "tax_amount": money(amount),
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
            "value": money(
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

    # Which machine each shift ran on. A variance is investigated by going to the
    # terminal, so a report that names only the cashier sends a manager looking
    # for a person rather than a drawer.
    device_ids = {t.device_id for t in tills if t.device_id}
    device_names: dict[str, str] = {}
    if device_ids:
        device_names = {
            str(d.id): d.name
            for d in (
                (await db.execute(select(Device).where(Device.id.in_(device_ids))))
                .scalars()
                .all()
            )
        }

    return [
        {
            "till_id": str(t.id),
            "business_date": t.business_date,
            "status": t.status,
            "user": labels.get(str(t.user_id), "Unknown"),
            "device_id": str(t.device_id) if t.device_id else None,
            "device_name": device_names.get(str(t.device_id)) if t.device_id else None,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
            "opening_amount": money(t.opening_amount),
            "estimated_cash": money(t.estimated_cash),
            "closing_amount": money(t.closing_amount)
            if t.closing_amount is not None
            else None,
            "variance": money(t.variance),
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
        {"type": op_type, "count": int(count or 0), "amount": money(amount)}
        for op_type, count, amount in (await db.execute(stmt)).all()
    ]
