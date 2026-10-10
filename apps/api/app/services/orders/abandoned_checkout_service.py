"""Abandoned-cart recovery: remind a customer to finish a checkout they started.

A customer can walk away at two points, and each leaves a different trace:

- **An unpaid order.** An online order is written as `created` before the
  customer is sent to the card gateway, and it only leaves `created` when the
  gateway's paid webhook arrives. The basket is emptied the moment the order is
  written, so the order *is* what they left behind.
- **A basket that never reached "Place order".** Lines in `cart_items`, a known
  email address, and no order.

Both are reminded once, `ABANDONED_CART_AFTER_MINUTES` after the customer last
put something in the basket — for an order, the moment it was written, since
its basket is gone. The link is the gateway's own still-live hosted payment page
when there is one (`provider.resume_url`), and the storefront's checkout
otherwise: the order's own retry screen for an unpaid order, the checkout for a
basket.

**Who is reachable.** Only a real address: a signed-up account's email, or the
address a guest typed into the checkout (`orders.email` / `carts.guest_email`).
A guest account's own email is a generated `…@guest.local` placeholder and is
never used.

**Never twice.** Three guards, in order:

1. Once per order (journal: `order_number` + `abandoned_cart`) and once per
   basket episode (journal: `reference` = the cart and the moment of its last
   add, template `abandoned_basket`). A basket that gains a line later is a new
   episode.
2. One reminder per recipient per `_RECIPIENT_COOLDOWN`, whichever kind it was.
   This is what stops a customer with a live Stripe session *and* a re-filled
   basket — or two unpaid orders — from getting two emails. Orders are swept
   before baskets, so where both are due the order's payment link wins.
3. Nothing from before `_START_AT`. The basket reminder is new, and every
   basket anyone ever abandoned is still in the table; without a floor the first
   tick after deploy would mail weeks-old baskets.

Shape borrowed from `log_retention` / `delivery_scheduler`: an advisory lock so
only one worker sweeps, a loop that survives a bad tick, and a heartbeat. The lock
is held WITHOUT pinning a work session across the per-order gateway and email
calls (those are third-party awaits) — candidates are read in one short session
that closes first, then the network work runs unlocked-from-the-DB but still under
the advisory lock.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, heartbeat
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.base import utcnow
from app.models.cart import Cart, CartItem
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum
from app.models.user import User
from app.services import cart_service, email_service
from app.services.orders import order_service
from app.services.payments import payment_service
from app.services.payments.payment_gateway_router import PROVIDERS

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 0x14
#: (was + 11, the catalog sync's key, so the two skipped each other).
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4814

#: The earliest abandonment this sweep will ever act on — midnight in Dubai on
#: the day the basket reminder shipped. Baskets and orders older than this are
#: history, not candidates.
_START_AT = datetime(2026, 9, 27, tzinfo=ZoneInfo("Asia/Dubai"))

#: At most one reminder of either kind to one address in this long.
_RECIPIENT_COOLDOWN = timedelta(hours=24)

_ORDER_TEMPLATE = "abandoned_cart.html"
_BASKET_TEMPLATE = "abandoned_basket.html"


@dataclass(frozen=True)
class _Basket:
    """One abandoned basket, read and rendered before any network call."""

    cart_id: uuid.UUID
    email: str
    last_added_at: datetime
    items: list[dict]
    subtotal: Decimal

    @property
    def reference(self) -> str:
        """The journal key for this episode: the cart and its last add."""
        return f"cart:{self.cart_id}:{int(self.last_added_at.timestamp())}"


def _window(now: datetime) -> tuple[datetime, datetime]:
    """`(oldest, newest)` abandonment moments that are due now."""
    oldest = max(
        now - timedelta(hours=max(settings.ABANDONED_CART_MAX_AGE_HOURS, 0)),
        _START_AT,
    )
    newest = now - timedelta(minutes=max(settings.ABANDONED_CART_AFTER_MINUTES, 0))
    return oldest, newest


def _real_email(address: str | None) -> str | None:
    """An address worth writing to, or None for a blank or a guest placeholder."""
    cleaned = (address or "").strip().lower()
    if not cleaned or "@" not in cleaned or cleaned.endswith("@guest.local"):
        return None
    return cleaned


async def _order_candidates(db, *, now: datetime) -> list[Order]:
    """Online orders still `created`, in the due window, with an email to send
    to. Items eager-loaded so `to_response` renders the cart without a lazy
    load."""
    oldest, newest = _window(now)
    rows = await db.scalars(
        select(Order)
        .where(
            Order.source == OrderSourceEnum.ONLINE.value,
            Order.status == OrderStatusEnum.CREATED,
            Order.email.is_not(None),
            Order.email != "",
            Order.created_at <= newest,
            Order.created_at >= oldest,
        )
        .order_by(Order.created_at)
        # Attempts too: a gateway whose resume link lives on the attempt row
        # (Paymob keeps its client secret in the stored checkout URL) reads them
        # after this session has closed, where a lazy load cannot happen — and
        # the paid guard in front of the checkout fallback reads them as well.
        .options(selectinload(Order.items), selectinload(Order.payment_transactions))
    )
    return list(rows)


async def _basket_candidates(db, *, now: datetime) -> list[_Basket]:
    """Baskets with lines, a real address, and a last add in the due window.

    "Last add" is the newest line's `created_at`, not `last_activity_at`: the
    reminder is timed from when the customer last put something in, and a page
    view touches the basket without adding anything."""
    oldest, newest = _window(now)
    last_add = func.max(CartItem.created_at).label("last_add")
    due = (
        select(CartItem.cart_id, last_add)
        .group_by(CartItem.cart_id)
        .having(last_add <= newest, last_add >= oldest)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Cart, due.c.last_add, User.email)
            .join(due, due.c.cart_id == Cart.id)
            .outerjoin(User, User.id == Cart.user_id)
            .options(selectinload(Cart.items).joinedload(CartItem.product))
            .order_by(due.c.last_add)
        )
    ).all()

    baskets: list[_Basket] = []
    for cart, last_added_at, account_email in rows:
        # The account's address where it is a real one; otherwise the one typed
        # at checkout — a guest account that gave no address at sign-in carries
        # a placeholder instead.
        email = _real_email(account_email) or _real_email(cart.guest_email)
        if email is None:
            continue
        lines = [item for item in cart.items if item.product is not None]
        if not lines:
            continue
        baskets.append(
            _Basket(
                cart_id=cart.id,
                email=email,
                last_added_at=last_added_at,
                items=[email_service.basket_item_row(item) for item in lines],
                subtotal=sum(
                    (cart_service.line_total(item) for item in lines), Decimal("0")
                ),
            )
        )
    return baskets


def _checkout_url(order: Order | None = None, *, locale: str = "en") -> str:
    """The storefront's own checkout — an unpaid order's retry screen, or the
    basket's checkout. Opened with the customer's saved session, which is where
    both live."""
    base = f"{settings.WEB_URL.rstrip('/')}/{locale}/checkout"
    if order is None:
        return base
    return f"{base}?step=payment&order_number={order.order_number}"


async def _order_link(order: Order) -> str | None:
    """Where an unpaid order's reminder should send the customer, or None to
    skip it.

    The gateway's live hosted page when it has one. Otherwise — an in-page Apple
    Pay attempt, an expired session, a gateway with no resume page — the
    order's retry screen on the checkout, but only once we are sure the money
    has not in fact moved: a missed success webhook leaves a paid order at
    `created` too, and "finish your checkout" is the wrong email for it."""
    provider = PROVIDERS.get(order.payment_provider or "")
    if provider is not None:
        resume_url = await provider.resume_url(order)
        if resume_url:
            return resume_url
    if payment_service._is_paid(order) or await payment_service._gateway_reports_paid(
        order
    ):
        return None
    locale = order.locale if order.locale in ("en", "ar") else "en"
    return _checkout_url(order, locale=locale)


async def _recently_reminded(email: str, now: datetime) -> bool:
    return await email_service.sent_to_recently(
        email, (_ORDER_TEMPLATE, _BASKET_TEMPLATE), since=now - _RECIPIENT_COOLDOWN
    )


async def sweep_once(now: datetime | None = None) -> int:
    """One pass: find abandoned checkouts and baskets and mail each one a link
    back. Returns the number of reminders sent. A no-op (0) when disabled or when
    another worker holds the lock."""
    if not settings.ABANDONED_CART_EMAIL_ENABLED:
        return 0
    now = now or utcnow()

    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="abandoned cart") as mine:
        if not mine:
            return 0

        # Read every candidate (and build its customer-facing response) inside one
        # short session that closes BEFORE any gateway/email call — DB-only work,
        # so the connection is never held idle across a third-party await.
        orders: list[tuple[Order, object]] = []
        async with AsyncSessionFactory() as db:
            for order in await _order_candidates(db, now=now):
                orders.append((order, await order_service.to_response(db, order)))
            baskets = await _basket_candidates(db, now=now)

        # Addresses mailed in this pass. The journal says the same thing once the
        # send is logged; this also covers a journal write that failed.
        reminded: set[str] = set()
        sent = 0

        # Orders first: where one customer has both, the order carries the live
        # payment link, and the recipient guard then holds the basket back.
        for order, response in orders:
            email = _real_email(order.email)
            try:
                if email is None or email in reminded:
                    continue
                # Once only: the journal already holds a sent `abandoned_cart` for
                # this order if a prior tick mailed it. Fails open (mails again)
                # only if the journal is unreachable, which is the safer error.
                if await email_service.already_sent(
                    order.order_number, _ORDER_TEMPLATE
                ):
                    continue
                if await _recently_reminded(email, now):
                    continue
                link = await _order_link(order)
                if not link:
                    continue
                await email_service.send_abandoned_cart(response, resume_url=link)
                reminded.add(email)
                sent += 1
            except Exception:  # noqa: BLE001 — one order must not stop the sweep
                logger.exception(
                    "abandoned-cart reminder failed for %s", order.order_number
                )

        for basket in baskets:
            try:
                if basket.email in reminded:
                    continue
                if await email_service.reference_sent(
                    basket.reference, _BASKET_TEMPLATE
                ):
                    continue
                if await _recently_reminded(basket.email, now):
                    continue
                await email_service.send_abandoned_basket(
                    to=basket.email,
                    items=basket.items,
                    subtotal=basket.subtotal,
                    checkout_url=_checkout_url(),
                    reference=basket.reference,
                )
                reminded.add(basket.email)
                sent += 1
            except Exception:  # noqa: BLE001 — one basket must not stop the sweep
                logger.exception(
                    "abandoned-basket reminder failed for cart %s", basket.cart_id
                )
        return sent


async def run_forever() -> None:
    """The loop. Cancelled on shutdown; never allowed to die on an exception."""
    interval = settings.ABANDONED_CART_SWEEP_MINUTES
    if interval <= 0:
        logger.info("Abandoned-cart sweep disabled (interval <= 0)")
        return
    logger.info(
        "Abandoned-cart sweep started (every %dm; nudge at %dm, max age %dh)",
        interval,
        settings.ABANDONED_CART_AFTER_MINUTES,
        settings.ABANDONED_CART_MAX_AGE_HOURS,
    )
    while True:
        try:
            # Sleeps first, like its scheduler neighbours: boot is the busiest
            # moment and nothing here is urgent to the second.
            await asyncio.sleep(interval * 60)
            await heartbeat.beat("abandoned_cart")
            sent = await sweep_once()
            if sent:
                logger.info("Abandoned-cart sweep sent %d reminder(s)", sent)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Abandoned-cart sweep tick failed")
