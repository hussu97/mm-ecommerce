"""
Which processor takes this card, decided at request time from a table.

The customer chose `card`. Everything after that is an operations decision, and
this module is where it is made. It exists so that "Stripe is having an
incident, move the estate to Ziina" is an admin toggle taken at 2am by whoever
is awake, rather than a branch, a review, a build and a deploy.

Three questions, in this order, and each one is a different kind of no:

1. **Is there a provider for this code?** A row naming a gateway nothing
   implements is a configuration mistake, and it is skipped loudly.
2. **Is it configured here?** Credentials are environment, not database. This
   is the check the admin cannot override, and it is what keeps production on
   Stripe while the same row exists everywhere.
3. **Does the amount fit?** Both processors refuse under AED 2.00 at their own
   edge, where the refusal is an opaque 400 in the last screen of checkout.
   Caught here it is a sentence the customer can act on.

A gateway in a staged rollout (`PAYMOB_ALLOWED_EMAILS`) adds a fourth: **is this
account on the list?** Off the list it is skipped as if it were unconfigured; on
it, it goes first regardless of priority, so testing it on production needs no
priority change that would also move everyone else.

Failover is the same walk, resumed. It is offered only for
`GatewayUnavailableError` — the gateway could not be reached, or answered 5xx —
and never for a refusal. Re-presenting a declined card to a second processor is
how you turn one honest decline into two, and if the second one takes it you
have an order paid through a gateway nobody chose.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models.payment_gateway import PaymentGateway, PaymentGatewayEnum
from app.services.providers.base import PaymentGatewayProvider
from app.services.providers.paymob_provider import provider as paymob_provider
from app.services.providers.stripe_provider import provider as stripe_provider
from app.services.providers.ziina_provider import provider as ziina_provider

logger = logging.getLogger(__name__)

__all__ = [
    "GatewayChoice",
    "NoGatewayAvailableError",
    "PROVIDERS",
    "candidates",
    "is_restricted",
    "select_gateway",
]


class NoGatewayAvailableError(BadRequestError):
    """
    Nothing can take this payment.

    A `BadRequestError` because it reaches the customer, and what they can do
    about it depends on why — an amount below every floor is theirs to fix, and
    both processors being down is not. The message says which.
    """


#: Every card gateway this application can actually drive.
#:
#: Membership here is *capability*, not permission. Permission is the
#: `payment_gateways` row plus the provider's own `is_configured()`, and both
#: are checked below on every single request rather than cached — a gateway
#: switched off in the admin has to stop taking traffic on the next checkout,
#: not on the next deploy.
PROVIDERS: dict[str, PaymentGatewayProvider] = {
    PaymentGatewayEnum.STRIPE.value: stripe_provider,
    PaymentGatewayEnum.ZIINA.value: ziina_provider,
    PaymentGatewayEnum.PAYMOB.value: paymob_provider,
}


@dataclass(frozen=True)
class GatewayChoice:
    """A gateway that may be used for this order, and the terms it carries."""

    row: PaymentGateway
    provider: PaymentGatewayProvider

    @property
    def code(self) -> str:
        return self.row.code

    @property
    def test_mode(self) -> bool:
        return bool(self.row.test_mode)


def _allowed_emails(code: str) -> frozenset[str] | None:
    """The accounts a gateway is limited to, or None when it is open to all."""
    if code != PaymentGatewayEnum.PAYMOB.value:
        return None
    emails = frozenset(
        e.strip().lower()
        for e in settings.PAYMOB_ALLOWED_EMAILS.split(",")
        if e.strip()
    )
    return emails or None


def is_restricted(code: str) -> bool:
    """Whether a gateway is in a staged rollout, offered to listed accounts only."""
    return _allowed_emails(code) is not None


async def candidates(
    db: AsyncSession, amount: Decimal, *, account_email: str | None = None
) -> list[GatewayChoice]:
    """
    Every gateway that could take *amount*, best first.

    The list rather than just the winner, because failover needs to know what
    comes next and computing it twice invites the two answers to disagree.

    *account_email* is the signed-in, non-guest account paying, or None. It
    only matters to a gateway in a staged rollout: off its list the gateway is
    skipped, on it the gateway is moved to the front.
    """
    email = account_email.strip().lower() if account_email else None
    rows = (
        (
            await db.execute(
                select(PaymentGateway)
                .where(PaymentGateway.is_active.is_(True))
                .order_by(PaymentGateway.priority, PaymentGateway.code)
            )
        )
        .scalars()
        .all()
    )

    usable: list[GatewayChoice] = []
    for row in rows:
        provider = PROVIDERS.get(row.code)
        if provider is None:
            logger.error(
                "payment_gateways row '%s' is active but nothing implements it "
                "— skipping",
                row.code,
            )
            continue
        if not provider.is_configured():
            # Expected on production for Ziina, and expected in reverse on a
            # laptop with no Stripe key. Info, not a warning: this is the
            # mechanism working, not a fault.
            logger.info(
                "Gateway '%s' is active but not configured in this environment "
                "— skipping",
                row.code,
            )
            continue
        if not _amount_fits(row, provider, amount):
            continue
        allowed = _allowed_emails(row.code)
        if allowed is not None and email not in allowed:
            continue
        usable.append(GatewayChoice(row=row, provider=provider))

    # Stable, so the unrestricted gateways keep their priority order behind it.
    usable.sort(key=lambda choice: not is_restricted(choice.code))
    return usable


async def select_gateway(
    db: AsyncSession, amount: Decimal, *, account_email: str | None = None
) -> GatewayChoice:
    """
    The gateway to try first for an order of *amount*.

    Raises `NoGatewayAvailableError` when there is none, with a message that
    distinguishes "your basket is too small" from "we cannot take cards right
    now", because those are not the same news.
    """
    options = await candidates(db, amount, account_email=account_email)
    if options:
        return options[0]

    floor = await _lowest_floor(db)
    if floor is not None and amount < floor:
        raise NoGatewayAvailableError(
            f"The order total after discount (AED {amount:.2f}) is below the "
            f"minimum chargeable amount of AED {floor:.2f}. "
            "Please add more items or adjust your discount."
        )

    logger.critical(
        "No payment gateway can take AED %s — every gateway is inactive, "
        "unconfigured, or out of range. Card checkout is down.",
        amount,
    )
    raise NoGatewayAvailableError(
        "Card payments are temporarily unavailable. Please try again shortly."
    )


def failover_after(
    tried: list[str], options: list[GatewayChoice]
) -> GatewayChoice | None:
    """
    The next gateway to try, given what has already failed.

    `supports_failover` is consulted here and not in `candidates`, because a
    gateway that may not be reached for automatically is still perfectly
    selectable as the deliberate first choice. Conflating the two would make
    "use this one, but never as a reflex" unexpressable.
    """
    for option in options:
        if option.code in tried:
            continue
        if not option.row.supports_failover:
            logger.info(
                "Gateway '%s' is available but opted out of automatic failover",
                option.code,
            )
            continue
        return option
    return None


# ── internals ─────────────────────────────────────────────────────────────────


def _floor_for(row: PaymentGateway, provider: PaymentGatewayProvider) -> Decimal | None:
    """
    The smallest amount this gateway will take.

    The row wins when it names one — that is the point of it being editable —
    and the provider's own constant is the fallback for a row created without
    one, so a processor's hard floor cannot be configured away by omission.
    """
    if row.min_amount is not None:
        return Decimal(str(row.min_amount))
    return provider.minimum_amount()


def _amount_fits(
    row: PaymentGateway, provider: PaymentGatewayProvider, amount: Decimal
) -> bool:
    floor = _floor_for(row, provider)
    if floor is not None and amount < floor:
        logger.info(
            "Gateway '%s' skipped: AED %s is below its floor of AED %s",
            row.code,
            amount,
            floor,
        )
        return False
    if row.max_amount is not None and amount > Decimal(str(row.max_amount)):
        logger.info(
            "Gateway '%s' skipped: AED %s is above its ceiling of AED %s",
            row.code,
            amount,
            row.max_amount,
        )
        return False
    return True


async def _lowest_floor(db: AsyncSession) -> Decimal | None:
    """
    The smallest amount *any* usable gateway would have taken.

    Only asked when nothing was selectable, to tell a too-small basket apart
    from an outage. Deliberately ignores the amount and keeps the configured
    check, so it reports a real number the customer can reach rather than the
    theoretical minimum of a gateway that has no keys.
    """
    rows = (
        (
            await db.execute(
                select(PaymentGateway).where(PaymentGateway.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    floors = [
        floor
        for row in rows
        if (provider := PROVIDERS.get(row.code)) is not None
        and provider.is_configured()
        and not is_restricted(row.code)
        and (floor := _floor_for(row, provider)) is not None
    ]
    return min(floors) if floors else None
