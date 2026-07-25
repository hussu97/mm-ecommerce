"""
Till lifecycle and cash reconciliation.

The drawer-operation ledger is the single source of truth for cash. Cash taken as
payment is written as a `sales` operation and cash refunded as a `return`, so the
expected drawer contents can always be recomputed from scratch:

    estimated_cash = opening_amount + Σ signed(drawer operations)

Nothing is incremented in place, so a terminal that crashes mid-sale cannot leave
the till permanently out of balance.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.till import (
    DRAWER_SIGN,
    DrawerOperation,
    DrawerOperationTypeEnum,
    Till,
    TillStatusEnum,
)
from app.models.user import User
from app.services import business_day_service

__all__ = [
    "add_drawer_operation",
    "build_report",
    "close_till",
    "estimated_cash",
    "get_open_till",
    "open_till",
]

ZERO = Decimal("0.00")


def _q(value: Decimal | float | int | str | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def get_open_till(
    db: AsyncSession, *, user_id: uuid.UUID, branch_id: uuid.UUID | None = None
) -> Till | None:
    stmt = select(Till).where(
        Till.user_id == user_id, Till.status == TillStatusEnum.OPEN.value
    )
    if branch_id is not None:
        stmt = stmt.where(Till.branch_id == branch_id)
    return (await db.execute(stmt.order_by(Till.opened_at.desc()))).scalars().first()


async def open_till(
    db: AsyncSession,
    *,
    user: User,
    branch: Branch,
    device_id: uuid.UUID | None,
    opening_amount: Decimal,
    notes: str | None = None,
) -> Till:
    """
    Open a till for `user` at `branch`.

    One open till per cashier: reopening while another is live would split their
    takings across two reconciliations.
    """
    existing = await get_open_till(db, user_id=user.id)
    if existing is not None:
        raise ConflictError(
            "You already have an open till. Close it before opening another."
        )

    if device_id is not None:
        device_conflict = (
            (
                await db.execute(
                    select(Till).where(
                        Till.device_id == device_id,
                        Till.status == TillStatusEnum.OPEN.value,
                    )
                )
            )
            .scalars()
            .first()
        )
        if device_conflict is not None:
            raise ConflictError("This device already has an open till")

    day = await business_day_service.get_or_open(db, branch, opened_by=user)

    till = Till(
        branch_id=branch.id,
        device_id=device_id,
        user_id=user.id,
        business_date=day.business_date,
        status=TillStatusEnum.OPEN.value,
        opened_at=utcnow(),
        opening_amount=_q(opening_amount),
        estimated_cash=_q(opening_amount),
        variance=ZERO,
        notes=notes,
    )
    db.add(till)
    await db.flush()
    await db.refresh(till)
    return till


async def estimated_cash(db: AsyncSession, till: Till) -> Decimal:
    """Recompute expected drawer contents from the ledger."""
    rows = (
        await db.execute(
            select(DrawerOperation.type, func.sum(DrawerOperation.amount))
            .where(DrawerOperation.till_id == till.id)
            .group_by(DrawerOperation.type)
        )
    ).all()

    total = _q(till.opening_amount)
    for op_type, amount in rows:
        total += _q(amount) * DRAWER_SIGN.get(op_type, 0)
    return _q(total)


async def add_drawer_operation(
    db: AsyncSession,
    *,
    till: Till,
    user: User,
    op_type: str,
    amount: Decimal,
    reason_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> DrawerOperation:
    if till.status != TillStatusEnum.OPEN.value:
        raise ConflictError("Cannot record drawer operations on a closed till")
    if op_type not in DRAWER_SIGN:
        raise ConflictError(f"Unknown drawer operation type '{op_type}'")

    # A no-sale drawer open carries no money; forcing zero keeps the ledger honest.
    recorded_amount = (
        ZERO if op_type == DrawerOperationTypeEnum.OPEN_DRAWER.value else _q(amount)
    )

    operation = DrawerOperation(
        till_id=till.id,
        user_id=user.id,
        type=op_type,
        amount=recorded_amount,
        reason_id=reason_id,
        order_id=order_id,
        notes=notes,
        recorded_at=utcnow(),
    )
    db.add(operation)
    await db.flush()

    till.estimated_cash = await estimated_cash(db, till)
    await db.flush()
    await db.refresh(operation)
    return operation


async def close_till(
    db: AsyncSession,
    *,
    till: Till,
    closed_by: User,
    closing_amount: Decimal,
    notes: str | None = None,
) -> Till:
    if till.status != TillStatusEnum.OPEN.value:
        raise ConflictError("This till is already closed")

    expected = await estimated_cash(db, till)
    counted = _q(closing_amount)

    till.estimated_cash = expected
    till.closing_amount = counted
    till.variance = _q(counted - expected)
    till.closed_at = utcnow()
    till.closed_by_id = closed_by.id
    till.status = TillStatusEnum.CLOSED.value
    if notes:
        till.notes = notes

    report = await build_report(db, till)
    till.totals = {
        "orders_count": report["orders_count"],
        "gross_sales": str(report["gross_sales"]),
        "discounts": str(report["discounts"]),
        "returns": str(report["returns"]),
        "charges": str(report["charges"]),
        "taxes": str(report["taxes"]),
        "net_sales": str(report["net_sales"]),
        "tips": str(report["tips"]),
        "payments_by_method": {
            k: str(v) for k, v in report["payments_by_method"].items()
        },
        "drawer_totals": {k: str(v) for k, v in report["drawer_totals"].items()},
    }

    await db.flush()
    await db.refresh(till)
    return till


async def build_report(db: AsyncSession, till: Till) -> dict:
    """
    X-report while the till is open, Z-report once closed.

    Sales figures are sourced from the drawer ledger and the POS order engine.
    Until an order carries POS payments (added with the order engine), the cash
    figures below are still exact because every cash movement is a drawer row.
    """
    drawer_rows = (
        await db.execute(
            select(DrawerOperation.type, func.sum(DrawerOperation.amount))
            .where(DrawerOperation.till_id == till.id)
            .group_by(DrawerOperation.type)
        )
    ).all()
    drawer_totals: dict[str, Decimal] = {
        op_type: _q(amount) for op_type, amount in drawer_rows
    }

    payments_by_method, sales = await _payment_breakdown(db, till)

    return {
        "till_id": till.id,
        "branch_id": till.branch_id,
        "business_date": till.business_date,
        "user_id": till.user_id,
        "opened_at": till.opened_at,
        "closed_at": till.closed_at,
        "opening_amount": _q(till.opening_amount),
        "estimated_cash": await estimated_cash(db, till),
        "closing_amount": (
            _q(till.closing_amount) if till.closing_amount is not None else None
        ),
        "variance": _q(till.variance) if till.closed_at else None,
        "orders_count": sales["orders_count"],
        "gross_sales": sales["gross_sales"],
        "discounts": sales["discounts"],
        "returns": sales["returns"],
        "charges": sales["charges"],
        "taxes": sales["taxes"],
        "net_sales": sales["net_sales"],
        "tips": sales["tips"],
        "payments_by_method": payments_by_method,
        "drawer_totals": drawer_totals,
    }


async def _payment_breakdown(
    db: AsyncSession, till: Till
) -> tuple[dict[str, Decimal], dict]:
    """
    Sales and tender breakdown for a till.

    Implemented against the drawer ledger so the till is reconcilable today; the
    POS order engine extends this with per-tender and per-tax detail.
    """
    cash_in = _q(
        (
            await db.execute(
                select(func.sum(DrawerOperation.amount)).where(
                    DrawerOperation.till_id == till.id,
                    DrawerOperation.type == DrawerOperationTypeEnum.SALES.value,
                )
            )
        ).scalar_one_or_none()
    )
    cash_out = _q(
        (
            await db.execute(
                select(func.sum(DrawerOperation.amount)).where(
                    DrawerOperation.till_id == till.id,
                    DrawerOperation.type == DrawerOperationTypeEnum.RETURN.value,
                )
            )
        ).scalar_one_or_none()
    )
    orders_count = int(
        (
            await db.execute(
                select(func.count(func.distinct(DrawerOperation.order_id))).where(
                    DrawerOperation.till_id == till.id,
                    DrawerOperation.order_id.isnot(None),
                )
            )
        ).scalar_one()
    )

    payments_by_method = {"cash": _q(cash_in - cash_out)} if cash_in or cash_out else {}
    sales = {
        "orders_count": orders_count,
        "gross_sales": _q(cash_in),
        "discounts": ZERO,
        "returns": _q(cash_out),
        "charges": ZERO,
        "taxes": ZERO,
        "net_sales": _q(cash_in - cash_out),
        "tips": ZERO,
    }
    return payments_by_method, sales


async def require_till(db: AsyncSession, till_id: uuid.UUID) -> Till:
    till = await db.get(Till, till_id)
    if till is None:
        raise NotFoundError("Till not found")
    return till
