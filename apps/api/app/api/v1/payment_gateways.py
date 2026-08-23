"""
The switch: which card processor is taking traffic, changeable without a deploy.

This is the operator-facing half of `payment_gateway_router`. It is small on
purpose — the interesting decisions are all in the router, and this is the
screen that lets someone make them at 2am.

Two rules are enforced here rather than left to whoever is clicking, because
both failure modes are silent and expensive:

* **A gateway with no credentials cannot be activated.** It would be selected by
  nothing (the router checks `is_configured()` on every request) while looking
  active in the admin, so the operator's mental model and the system's
  behaviour would silently disagree. This is also the rule that means the Ziina
  toggle can exist on production and do nothing.
* **The last active gateway cannot be switched off.** That is not a
  configuration, it is an outage, and it should take more than one click and no
  warning.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.permissions import require
from app.models.payment_gateway import PaymentGateway
from app.models.user import User
from app.services import audit_service
from app.services.payments.payment_gateway_router import PROVIDERS

logger = logging.getLogger(__name__)

router = APIRouter()


class PaymentGatewayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    is_active: bool
    priority: int
    supports_failover: bool
    min_amount: Decimal | None
    max_amount: Decimal | None
    test_mode: bool

    #: Whether this environment actually holds credentials for it.
    #:
    #: Computed, never stored — it is a fact about the container, and a column
    #: would be a copy of it that goes stale the moment a secret rotates. This
    #: is what the admin renders as "not configured here", and it is why the
    #: Ziina row is visible and unusable on production.
    is_configured: bool

    #: Whether the router would actually pick it right now.
    is_routable: bool


class PaymentGatewayUpdate(BaseModel):
    is_active: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=999)
    supports_failover: bool | None = None
    min_amount: Decimal | None = Field(default=None, ge=0)
    max_amount: Decimal | None = Field(default=None, ge=0)
    test_mode: bool | None = None


def _to_response(row: PaymentGateway) -> PaymentGatewayResponse:
    provider = PROVIDERS.get(row.code)
    configured = bool(provider and provider.is_configured())
    return PaymentGatewayResponse(
        code=row.code,
        name=row.name,
        is_active=row.is_active,
        priority=row.priority,
        supports_failover=row.supports_failover,
        min_amount=row.min_amount,
        max_amount=row.max_amount,
        test_mode=row.test_mode,
        is_configured=configured,
        is_routable=configured and row.is_active,
    )


@router.get("", response_model=list[PaymentGatewayResponse])
async def list_gateways(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("admin.payments.manage")),
):
    """Every card gateway, in the order the router would walk them."""
    rows = (
        (
            await db.execute(
                select(PaymentGateway).order_by(
                    PaymentGateway.priority, PaymentGateway.code
                )
            )
        )
        .scalars()
        .all()
    )
    return [_to_response(row) for row in rows]


@router.patch("/{code}", response_model=PaymentGatewayResponse)
async def update_gateway(
    code: str,
    data: PaymentGatewayUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.payments.manage")),
):
    """Change how — or whether — a gateway is used. Takes effect immediately."""
    row = (
        await db.execute(
            select(PaymentGateway).where(PaymentGateway.code == code.lower())
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Payment gateway '{code}' not found")

    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return _to_response(row)

    before = {
        "is_active": row.is_active,
        "priority": row.priority,
        "supports_failover": row.supports_failover,
        "min_amount": str(row.min_amount) if row.min_amount is not None else None,
        "max_amount": str(row.max_amount) if row.max_amount is not None else None,
        "test_mode": row.test_mode,
    }

    if changes.get("is_active") is True and not row.is_active:
        await _assert_can_activate(row)
    if changes.get("is_active") is False and row.is_active:
        await _assert_not_the_last_one(db, row)

    for field, value in changes.items():
        setattr(row, field, value)

    if (
        row.min_amount is not None
        and row.max_amount is not None
        and row.min_amount > row.max_amount
    ):
        raise BadRequestError(
            "The minimum amount cannot be above the maximum — that range takes "
            "no orders at all."
        )

    await db.flush()
    await db.refresh(row)

    logger.warning(
        "Payment gateway '%s' changed by %s: %s → %s",
        row.code,
        admin.email,
        before,
        changes,
    )
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="payment_gateway",
        entity_id=row.code,
        entity_label=row.name,
        admin=admin,
        changes={"before": before, "after": changes},
        request=request,
    )
    return _to_response(row)


async def _assert_can_activate(row: PaymentGateway) -> None:
    provider = PROVIDERS.get(row.code)
    if provider is None:
        raise BadRequestError(
            f"Nothing implements gateway '{row.code}' — it cannot be activated."
        )
    if not provider.is_configured():
        raise BadRequestError(
            f"{row.name} has no credentials in this environment, so activating "
            "it would take no traffic while appearing to. Set its API key and "
            "webhook secret first."
        )


async def _assert_not_the_last_one(db: AsyncSession, row: PaymentGateway) -> None:
    """
    Refuse to switch off the only gateway that is actually usable.

    Counted against `is_configured` rather than against `is_active` alone,
    because a second row that is active and has no keys is not a fallback — it
    is the same outage with an extra step.
    """
    others = (
        (
            await db.execute(
                select(PaymentGateway).where(
                    PaymentGateway.is_active.is_(True),
                    PaymentGateway.code != row.code,
                )
            )
        )
        .scalars()
        .all()
    )
    usable = [
        other
        for other in others
        if (provider := PROVIDERS.get(other.code)) is not None
        and provider.is_configured()
    ]
    if not usable:
        raise BadRequestError(
            f"{row.name} is the only working card gateway — switching it off "
            "would stop card checkout entirely. Activate another one first."
        )
