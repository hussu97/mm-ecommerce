"""Confirmation emails that never went out, sent late rather than never.

An order's confirmation — the customer's email and the owners' notification —
is sent *after* the transaction that confirmed it commits (`app.core.post_commit`,
via `order_service.send_confirmation_emails_after_commit`), so a payment webhook
does not hold the order row and the branch's inventory lock across Resend. The
price of that is a gap: a process that dies between the `COMMIT` and the send
loses the send, and the gateway's retry will not repeat it — the dedup row was
committed with the confirmation.

This closes the gap from the other side. Each delivery-scheduler tick it looks
for storefront orders confirmed by a payment or a checkout between
`_MAX_AGE` and `_MIN_AGE` ago whose journal (`email_logs`) holds **no row at
all** for the confirmation or for the owners' notification, and sends whichever
is missing.

* **No row at all, not "no sent row".** A `failed` row means the send was
  attempted and the provider refused; the funnel's policy for that is to make it
  loud (`report_failed_sends`), not to retry. Only an attempt that left no trace
  is what a crash looks like.
* **`_MIN_AGE`** keeps it off a send that is merely still in flight.
* **`_MAX_AGE`** is far inside `LOG_RETENTION_DAYS` (the journal is purged at a
  week), so a purged row can never read as a missing email, and an email hours
  late is still worth having where one a day late is noise.
* **Confirmed by `payment` or `checkout` only** — the two paths that send both
  emails. An admin confirming a bank transfer by hand sends the customer's
  email and not the owners' (they are the ones who did it), and must not be
  topped up with one.
* **Still going.** An order cancelled or refunded since is not confirmed to
  anybody now.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.email_log import EmailLog, EmailTemplate
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import OrderStatusEvent, StatusSourceEnum
from app.models.pos_order import OrderSourceEnum
from app.schemas.order import OrderResponse
from app.services import email_service
from app.services.orders import channels, order_service

logger = logging.getLogger(__name__)

__all__ = ["Owed", "find_unsent", "send"]

_MIN_AGE = timedelta(minutes=10)
_MAX_AGE = timedelta(hours=6)

#: Who confirmed it, for the orders that send both emails on confirmation.
_CONFIRMED_BY = (StatusSourceEnum.PAYMENT.value, StatusSourceEnum.CHECKOUT.value)

#: Where an order can be now and still want telling it was confirmed.
_STILL_GOING = (
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.ARRIVED_AT_POS,
    OrderStatusEnum.PACKED,
    OrderStatusEnum.OUT_FOR_DELIVERY,
    OrderStatusEnum.DELIVERED,
)

_CUSTOMER = EmailTemplate.ORDER_CONFIRMATION.value
_OWNER = EmailTemplate.OWNER_ORDER_NOTIFICATION.value

#: Orders this process has already made good. The journal row a send writes is
#: what stops a repeat; this covers a journal write that failed, so a broken
#: `email_logs` insert cannot become an email every minute for six hours.
_ATTEMPTED: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class Owed:
    """One order and which of its two confirmation emails left no trace."""

    order: OrderResponse
    customer: bool
    owner: bool


def _logged(template: str):
    return exists().where(
        EmailLog.order_number == Order.order_number,
        EmailLog.template == template,
    )


async def find_unsent(db: AsyncSession, *, now: datetime | None = None) -> list[Owed]:
    """The orders owed a confirmation email, rendered while the session is open.

    Database only — the caller ends its transaction before `send` goes to the
    network, so no connection idles in a transaction across Resend."""
    now = now or utcnow()
    oldest, newest = now - _MAX_AGE, now - _MIN_AGE
    customer_logged, owner_logged = _logged(_CUSTOMER), _logged(_OWNER)
    rows = (
        await db.execute(
            select(Order.id, ~customer_logged, ~owner_logged)
            .where(
                Order.source == OrderSourceEnum.ONLINE.value,
                Order.status.in_(_STILL_GOING),
                # Index-backed prefilter: a payment confirms within days of the
                # checkout that created the order (Stripe retries for three).
                Order.created_at >= oldest - timedelta(days=4),
                exists().where(
                    OrderStatusEvent.order_id == Order.id,
                    OrderStatusEvent.status == OrderStatusEnum.CONFIRMED.value,
                    OrderStatusEvent.source.in_(_CONFIRMED_BY),
                    OrderStatusEvent.at >= oldest,
                    OrderStatusEvent.at <= newest,
                ),
                ~customer_logged | ~owner_logged,
            )
            .order_by(Order.created_at)
        )
    ).all()

    owed: list[Owed] = []
    for order_id, customer_missing, owner_missing in rows:
        order = await order_service.get_for_notification(db, order_id)
        if order is None:  # pragma: no cover — selected a moment ago
            continue
        response = await order_service.to_response(db, order)
        customer = (
            customer_missing
            and (response.order_number, _CUSTOMER) not in _ATTEMPTED
            and email_service.customer_email_allowed(response, _CUSTOMER)
        )
        owner = (
            owner_missing
            and (response.order_number, _OWNER) not in _ATTEMPTED
            and channels.policy_for(response.source).notifies_owner_on_order
        )
        if customer or owner:
            owed.append(Owed(order=response, customer=customer, owner=owner))
    return owed


async def send(owed: list[Owed]) -> list[str]:
    """Send what each order is owed. Returns the order numbers made good.

    Never raises: `email_service` sends never do, and one order is not allowed
    to stop the rest."""
    done: list[str] = []
    for item in owed:
        number = item.order.order_number
        try:
            if item.customer:
                _ATTEMPTED.add((number, _CUSTOMER))
                await email_service.send_order_confirmation(item.order)
            if item.owner:
                _ATTEMPTED.add((number, _OWNER))
                await email_service.send_owner_order_notification(item.order)
        except Exception:  # noqa: BLE001 — the next order still gets its email
            logger.exception("Could not make good the confirmation for %s", number)
            continue
        logger.warning(
            "Confirmation for %s had never been sent (customer=%s owner=%s) — "
            "sent it now",
            number,
            item.customer,
            item.owner,
        )
        done.append(number)
    return done
