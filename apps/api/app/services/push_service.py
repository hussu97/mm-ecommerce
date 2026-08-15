"""
Telling a branch's registers that something happened.

Two events matter, and they are the two the shop cannot afford to miss: an order
has arrived, and a rider has been assigned to one. Everything else the register
can find out by looking.

**A push is a nudge, never the record.** The order is already written and already
visible to anyone who pulls the pending list; a notification that fails to send
must not fail the order, so nothing here raises into a caller. That matters more
than usual because a push crosses a network we do not control on the way to a
device that may be in a drawer.

**Dead tokens are retired, not retried.** APNs answers `BadDeviceToken` or 410
for a device that has uninstalled, restored, or simply been reissued a token by
iOS. Sending to it again is guaranteed to fail, so the row is marked revoked and
skipped from then on.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.device_push_token import DevicePushToken
from app.models.order import Order
from app.services.providers.apns_provider import ApnsError, provider

logger = logging.getLogger(__name__)

__all__ = [
    "is_enabled",
    "notify_order_placed",
    "notify_rider_assigned",
    "register_token",
    "tokens_for_branch",
]


def is_enabled() -> bool:
    return provider.is_configured


async def register_token(
    db: AsyncSession,
    *,
    token: str,
    bundle_id: str,
    branch_id: uuid.UUID | None,
    device_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    is_sandbox: bool = False,
) -> DevicePushToken:
    """
    Record a device, or bring an existing registration up to date.

    Upsert on the token rather than insert: an app re-registers on every launch,
    and a row per launch would mean the same iPad buzzing five times. A device
    that had been revoked and is registering again is alive, so it is un-revoked.
    """
    existing = (
        (
            await db.execute(
                select(DevicePushToken).where(DevicePushToken.token == token)
            )
        )
        .scalars()
        .first()
    )

    now = datetime.now(timezone.utc)
    if existing is None:
        existing = DevicePushToken(token=token)
        db.add(existing)

    existing.bundle_id = bundle_id
    existing.branch_id = branch_id
    existing.device_id = device_id
    existing.user_id = user_id
    existing.is_sandbox = is_sandbox
    existing.last_seen_at = now
    existing.revoked_at = None
    existing.revoked_reason = None
    await db.flush()
    return existing


async def tokens_for_branch(
    db: AsyncSession, branch_id: uuid.UUID
) -> list[DevicePushToken]:
    result = await db.execute(
        select(DevicePushToken).where(
            DevicePushToken.branch_id == branch_id,
            DevicePushToken.revoked_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def _send_to_branch(
    db: AsyncSession,
    branch_id: uuid.UUID | None,
    *,
    payload: dict,
    collapse_id: str,
) -> int:
    """Deliver to every live register on a branch. Returns how many landed."""
    if branch_id is None:
        return 0
    if not is_enabled():
        logger.info("APNs is not configured; not notifying branch %s", branch_id)
        return 0

    delivered = 0
    for row in await tokens_for_branch(db, branch_id):
        try:
            result = await provider.send(
                token=row.token,
                bundle_id=row.bundle_id,
                payload=payload,
                sandbox=row.is_sandbox,
                collapse_id=collapse_id,
            )
        except ApnsError as exc:  # configuration, not delivery
            logger.warning("APNs refused the whole send: %s", exc)
            return delivered
        except Exception:  # pragma: no cover — defensive
            logger.exception("Unexpected error pushing to %s…", row.token[:12])
            continue

        if result.delivered:
            delivered += 1
        elif result.dead:
            # Guaranteed to fail forever. Retire it so the next order does not
            # spend a request on a device that no longer exists.
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = result.reason[:100]
            logger.info(
                "Retired dead push token %s…: %s", row.token[:12], result.reason
            )

    return delivered


def _alert(
    title: str,
    body: str,
    *,
    sound: str,
    extra: dict,
    wake_the_app: bool = False,
) -> dict:
    aps: dict = {
        "alert": {"title": title, "body": body},
        "sound": sound,
        # Cuts through a Focus mode. An order arriving is the one thing a
        # counter iPad exists to be told about, and "time-sensitive" is what
        # iOS calls exactly that.
        "interruption-level": "time-sensitive",
    }
    if wake_the_app:
        # Runs the app's background handler as well as showing the banner, which
        # is what lets a backgrounded iPad fetch the order and put it on the
        # printer instead of waiting to be picked up. Needs the
        # `remote-notification` background mode in the receiving app — see
        # `MMPosPad/Info.plist` — and does nothing at all without it.
        #
        # Alongside the alert rather than instead of it. A push that is *only*
        # `content-available` is throttled by iOS at its own discretion and is
        # not shown to anybody; the alarm cannot be built on something the
        # system is free to deliver in an hour.
        aps["content-available"] = 1
    return {"aps": aps, **extra}


async def notify_order_placed(db: AsyncSession, order: Order) -> int:
    """
    A website order has landed on this branch's register.

    Uses the app's own alert sound rather than the default. The requirement is a
    tone that keeps going until somebody dismisses it, and iOS will not loop a
    notification sound — so the push carries the *event*, and the app plays and
    repeats the sound itself once it is open. A push alone can only be the
    doorbell, never the alarm.
    """
    if not order.branch_id:
        return 0

    name = order.customer_name or "A customer"
    payload = _alert(
        "New online order",
        f"#{order.check_number or order.order_number} · {name} · AED {order.total}",
        sound=settings.APNS_ORDER_SOUND,
        extra={
            "event": "order_placed",
            "order_number": order.order_number,
            "order_id": str(order.id),
            "check_number": order.check_number,
            "branch_id": str(order.branch_id),
            # The app starts its own repeating tone off this, and stops when the
            # cashier accepts the order.
            "requires_acknowledgement": True,
        },
        # The one push worth waking a suspended app for. A terminal set to accept
        # by itself does the accept and the print inside the handler this
        # enables, so a receipt reaches the kitchen whether or not anybody has
        # the app open.
        wake_the_app=True,
    )
    sent = await _send_to_branch(
        db,
        order.branch_id,
        payload=payload,
        # One order, one notification, however many times we are asked to send.
        collapse_id=f"order-{order.order_number}",
    )
    logger.info(
        "Notified %s register(s) at branch %s about %s",
        sent,
        order.branch_id,
        order.order_number,
    )
    return sent


async def notify_rider_assigned(
    db: AsyncSession, order: Order, *, rider_name: str | None = None
) -> int:
    """A courier has a name and is on the way to collect."""
    if not order.branch_id:
        return 0

    who = rider_name or "A rider"
    payload = _alert(
        "Rider assigned",
        f"{who} is collecting #{order.check_number or order.order_number}",
        sound="default",
        extra={
            "event": "rider_assigned",
            "order_number": order.order_number,
            "order_id": str(order.id),
            "branch_id": str(order.branch_id),
            # Informational: nothing to dismiss, no tone to repeat.
            "requires_acknowledgement": False,
        },
    )
    return await _send_to_branch(
        db,
        order.branch_id,
        payload=payload,
        collapse_id=f"rider-{order.order_number}",
    )
