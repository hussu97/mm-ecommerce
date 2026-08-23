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

from app.core.money import money

from sqlalchemy import case, func, select
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
from app.models.order import Order, OrderItem
from app.models.payment_method import PaymentMethod
from app.models.pos_order import OrderPayment, PosOrderStatusEnum
from app.models.user import User
from app.services import business_day_service

__all__ = [
    "add_drawer_operation",
    "build_report",
    "close_till",
    "estimated_cash",
    "get_open_till",
    "handover_on_device",
    "open_till",
    "open_till_on_device",
]

ZERO = Decimal("0.00")


async def get_open_till(
    db: AsyncSession, *, user_id: uuid.UUID, branch_id: uuid.UUID | None = None
) -> Till | None:
    stmt = select(Till).where(
        Till.user_id == user_id, Till.status == TillStatusEnum.OPEN.value
    )
    if branch_id is not None:
        stmt = stmt.where(Till.branch_id == branch_id)
    return (await db.execute(stmt.order_by(Till.opened_at.desc()))).scalars().first()


async def open_till_on_device(
    db: AsyncSession, device_id: uuid.UUID | None
) -> Till | None:
    """The till open on this terminal, whoever it belongs to."""
    if device_id is None:
        return None
    return (
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


async def handover_on_device(
    db: AsyncSession,
    *,
    device_id: uuid.UUID | None,
    user: User,
    counted: Decimal,
) -> Till | None:
    """
    Hand a terminal from the cashier who left it open to the one signing in.

    Returns the till it closed, or `None` when there was nothing to hand over.

    A shop shares one iPad across a shift change, and the outgoing cashier does
    not always close their till before going home. Until this existed that
    stranded the terminal: `/tills/current` is scoped to the caller, so the
    incoming cashier is shown "Open your till" with no till of their own, and
    opening one was refused because the *device* already had one. They could not
    reach the register to close the other person's either — Close till lives
    behind an open till — so the counter iPad was dead until the original
    cashier came back and signed in.

    The drawer is counted exactly once at a handover, and that one count means
    both things: it is what the outgoing till closed on, and what the incoming
    one opens with. So the amount the new cashier types is passed straight
    through as the old till's closing amount, which is what produces its
    variance and its Z report — the handover is reconciled, not waved through.

    Deliberately not gated on a permission or a business date. Whoever is
    standing at the terminal with the drawer counted is the person who can say
    what is in it, and a till left open across midnight is the same problem as
    one left open across a shift. What it does *not* do is close over unsettled
    checks: `close_till` refuses that without `pos.till.manage`, and its refusal
    names the number of open checks, which is a far better place to be stuck
    than a screen with no way forward.
    """
    outgoing = await open_till_on_device(db, device_id)
    if outgoing is None or outgoing.user_id == user.id:
        return None

    return await close_till(
        db,
        till=outgoing,
        closed_by=user,
        closing_amount=counted,
        notes=(
            "Closed at handover by "
            f"{user.display_name or user.email}, who counted the drawer."
        ),
    )


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

    # A till still open on this terminal belongs to somebody else — the caller's
    # own was caught above. `handover_on_device` is what clears it, and the
    # endpoint runs that first so it can audit the close; this is the net that
    # catches a caller who did not.
    if await open_till_on_device(db, device_id) is not None:
        raise ConflictError("This device already has an open till")

    day = await business_day_service.get_or_open(db, branch, opened_by=user)

    till = Till(
        branch_id=branch.id,
        device_id=device_id,
        user_id=user.id,
        business_date=day.business_date,
        status=TillStatusEnum.OPEN.value,
        opened_at=utcnow(),
        opening_amount=money(opening_amount),
        estimated_cash=money(opening_amount),
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

    total = money(till.opening_amount)
    for op_type, amount in rows:
        total += money(amount) * DRAWER_SIGN.get(op_type, 0)
    return money(total)


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
        ZERO if op_type == DrawerOperationTypeEnum.OPEN_DRAWER.value else money(amount)
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


#: Register states that still owe the till something. `closed`, `void` and
#: `joined` are finished and do not hold a shift open.
_UNSETTLED = (
    PosOrderStatusEnum.DRAFT.value,
    PosOrderStatusEnum.PENDING.value,
    PosOrderStatusEnum.ACTIVE.value,
)


async def _open_order_count(db: AsyncSession, till: Till) -> int:
    """Checks still open against this till."""
    return int(
        (
            await db.execute(
                select(func.count(Order.id)).where(
                    Order.till_id == till.id,
                    Order.pos_status.in_(_UNSETTLED),
                )
            )
        ).scalar()
        or 0
    )


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

    # A till closed over an unpaid check strands that sale: the money was never
    # taken, the drawer is counted without it, and the check stays open against
    # a shift that has been reported and signed off. `pos.till.manage` (then
    # `pos.till.close_with_active_orders`) covers exactly this, and the register only
    # ever disabled the button for the one check on screen — so a split check,
    # which by construction leaves a half the cashier cannot see, was precisely
    # the case that slipped through.
    open_orders = await _open_order_count(db, till)
    if open_orders and not (closed_by.is_admin or closed_by.can("pos.till.manage")):
        raise ConflictError(
            f"{open_orders} check(s) are still open on this till. "
            "Settle or void them first, or ask a manager to close over them."
        )

    expected = await estimated_cash(db, till)
    counted = money(closing_amount)

    till.estimated_cash = expected
    till.closing_amount = counted
    till.variance = money(counted - expected)
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
        op_type: money(amount) for op_type, amount in drawer_rows
    }

    payments_by_method, sales = await _payment_breakdown(db, till)

    return {
        "till_id": till.id,
        "branch_id": till.branch_id,
        "business_date": till.business_date,
        "user_id": till.user_id,
        "opened_at": till.opened_at,
        "closed_at": till.closed_at,
        "opening_amount": money(till.opening_amount),
        "estimated_cash": await estimated_cash(db, till),
        "closing_amount": (
            money(till.closing_amount) if till.closing_amount is not None else None
        ),
        "variance": money(till.variance) if till.closed_at else None,
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

    Read from the orders closed on this till, not from the drawer ledger: the
    ledger only ever sees cash, so a shift paid half on card reported no card
    tender, no VAT and no discounts on its X and Z reports. Cash reconciliation
    still comes from the ledger — see `estimated_cash` — because that is the
    only thing the drawer contents can be checked against.

    Figures use the same definitions as `pos_reports_service.sales_summary`, so
    a Z report and the day's sales report agree.
    """
    row = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.subtotal), 0),
                func.coalesce(func.sum(Order.discount_amount), 0),
                func.coalesce(func.sum(Order.charges_amount), 0),
                func.coalesce(func.sum(Order.vat_amount), 0),
                func.coalesce(func.sum(Order.tips_amount), 0),
                func.coalesce(func.sum(Order.total), 0),
            ).where(
                Order.till_id == till.id,
                Order.pos_status == PosOrderStatusEnum.CLOSED.value,
            )
        )
    ).one()
    orders_count, subtotal, discounts, charges, vat, tips, total = row

    returns = money(
        (
            await db.execute(
                select(
                    func.coalesce(
                        func.sum(OrderItem.returned_quantity * OrderItem.unit_price), 0
                    )
                )
                # The select list only mentions OrderItem, so the left side of
                # the join has to be stated explicitly.
                .select_from(Order)
                .join(OrderItem, OrderItem.order_id == Order.id)
                .where(
                    Order.till_id == till.id,
                    Order.pos_status == PosOrderStatusEnum.CLOSED.value,
                )
            )
        ).scalar_one()
    )

    # Every tender that touched this till, named as the cashier saw it.
    # Filtered on the payment's own till, not the order's: a check opened on one
    # till can be settled on another, and the money belongs where it was taken.
    # Refunds are netted off rather than counted as takings.
    tender_rows = (
        await db.execute(
            select(
                PaymentMethod.name,
                func.coalesce(
                    func.sum(
                        case(
                            (OrderPayment.is_refund.is_(True), -OrderPayment.amount),
                            else_=OrderPayment.amount,
                        )
                    ),
                    0,
                ),
            )
            .select_from(OrderPayment)
            .join(PaymentMethod, PaymentMethod.id == OrderPayment.payment_method_id)
            .where(OrderPayment.till_id == till.id)
            .group_by(PaymentMethod.name)
        )
    ).all()

    payments_by_method = {name: money(amount) for name, amount in tender_rows}
    sales = {
        "orders_count": int(orders_count or 0),
        "gross_sales": money(subtotal),
        "discounts": money(discounts),
        "returns": returns,
        "charges": money(charges),
        "taxes": money(vat),
        "net_sales": money(total),
        "tips": money(tips),
    }
    return payments_by_method, sales


async def require_till(db: AsyncSession, till_id: uuid.UUID) -> Till:
    till = await db.get(Till, till_id)
    if till is None:
        raise NotFoundError("Till not found")
    return till
