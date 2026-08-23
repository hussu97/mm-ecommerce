"""
Live baskets — the one panel that is not about the past.

The rest of the analytics screen answers questions about `orders`, and is
cached for five minutes because the past does not move. This answers the
opposite question — what is in people's hands *right now* — and is deliberately
outside that arrangement:

* **Uncached.** A basket abandoned four minutes ago shown as still active is
  the one error this screen cannot afford, because the whole point of it is
  the age of the row. `_ANALYTICS_TTL` would put a five-minute lie on the only
  column anybody reads.
* **`customers.read`, not `reports.sales`.** The rest of the dashboard is
  aggregate; this names individual people and carries their email addresses.
  The permission that governs "see who a customer is" is the one that should
  open it — the sales-report permission was never granted with that in mind.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core import search as search_text
from app.core.deps import get_db
from app.core.permissions import require
from app.models.cart import Cart, CartItem
from app.models.order import DeliveryMethodEnum
from app.models.user import User
from app.services import cart_service
from app.services.delivery import delivery_service
from app.services.orders import order_pricing

router = APIRouter()


class LiveCartLine(BaseModel):
    """One line of a basket somebody is still holding."""

    product_id: uuid.UUID
    product_name: str
    product_sku: str | None = None
    quantity: int
    #: Product plus the options chosen on it, priced by the same
    #: `cart_service.line_unit_price` the storefront basket renders from.
    unit_price: float
    line_total: float
    #: The chosen options, as words: `["Filling: Ferrero ×2", "Size: 9\""]`.
    #: Read off the line's own snapshot, so it says what was chosen at the time
    #: even if the modifier has since been renamed.
    options: list[str] = []
    #: What the customer asked to have written, for a gift line.
    personalisation_note: str | None = None


class LiveCart(BaseModel):
    """
    One basket that currently holds items, and everything needed to judge it.
    """

    id: uuid.UUID
    #: The account holding it, when there is one.
    user_id: uuid.UUID | None = None
    #: The browser holding it, when there is not.
    session_id: str | None = None

    #: Where to write, or `null` for a basket nobody can be reached about.
    #:
    #: The account's address where the basket has a `user_id`, otherwise the one
    #: typed into the checkout form. Never both — see `Cart.guest_email`.
    email: str | None = None
    #: `account` or `checkout`, saying which of the two the address above came
    #: from. `null` when there is no address. Worth showing: a `checkout`
    #: address means the shopper reached the form and turned back, which is a
    #: different and much warmer thing than a basket left on a product page.
    email_source: str | None = None
    #: False for a basket held by a browser with no account, and for the guest
    #: account a checkout mints — neither has a password or an order history.
    #:
    #: There is no name to show alongside: `users` carries an email and a phone
    #: for a customer and nothing else. A name is typed per delivery address,
    #: and a basket has no address yet — which is most of why it is still a
    #: basket.
    is_registered: bool = False

    lines: list[LiveCartLine] = []
    #: Units, not lines: two of one cake and one of another is three.
    item_count: int = 0

    #: Goods only. No fees, no VAT, no discount.
    subtotal: float
    #: The small-basket surcharge this basket would attract on a delivery,
    #: from `order_pricing.low_order_fee_for` — the same function that charges
    #: it. Zero above the threshold.
    low_order_fee: float = 0.0
    #: What a courier quoted for this basket while the shopper was deciding, if
    #: they got as far as dropping a pin. Null otherwise: there is no honest
    #: delivery fee for an address nobody has given yet, and inventing one would
    #: put a number on this screen the shop never quoted.
    delivery_fee: float | None = None
    #: The zone that estimate was taken against, for the same reason.
    delivery_zone: str | None = None
    #: `subtotal + low_order_fee + delivery_fee`, with the fees that are known.
    #:
    #: **Excludes any promo discount**, including when `promo_code` is set. What
    #: a coupon is worth depends on the basket, the identity and the day, and it
    #: is decided at the checkout every time it is asked (migration 115). A
    #: figure here would be a second answer, quoted from a screen that cannot
    #: see the first.
    estimated_total: float

    #: The code applied in the basket. The code, never the discount.
    promo_code: str | None = None

    created_at: datetime
    #: When the shopper last touched this basket. See `Cart.last_activity_at`.
    last_activity_at: datetime | None = None
    #: How long it has been sitting untouched, in whole minutes. Computed here
    #: so every reader agrees on it and so the table can be sorted and filtered
    #: on one number rather than on a clock difference each client works out.
    idle_minutes: int | None = None


class LiveCartsSummary(BaseModel):
    """
    The header figures — and, read together, the abandoned-cart business case.

    `reachable_value` against `total_value` is the whole question: it is what
    a recovery email could be sent about, as against what is sitting there.
    Both are over the filtered set, so narrowing to "idle more than an hour"
    narrows these too, which is the comparison worth making.
    """

    carts: int
    #: Goods value across every basket matching the filters.
    total_value: float
    #: How many of them carry an address.
    with_email: int
    #: Goods value of just those.
    reachable_value: float
    idle_over_1h: int
    idle_over_24h: int


class LiveCartsResponse(BaseModel):
    items: list[LiveCart]
    total: int
    page: int
    per_page: int
    pages: int
    summary: LiveCartsSummary


def _option_words(option: dict) -> str:
    """One snapshot row as the console should read it."""
    modifier = option.get("modifier_name") or ""
    name = option.get("option_name") or "?"
    quantity = int(option.get("quantity") or 1)
    label = f"{modifier}: {name}" if modifier else name
    return f"{label} ×{quantity}" if quantity > 1 else label


def _cart_email(cart: Cart) -> tuple[str | None, str | None]:
    """
    Where to write about this basket, and where that address came from.

    The account first. `Cart.guest_email` is only ever written for a basket
    without one, so in practice these never compete — the precedence is stated
    here so that a row restored from a dump that predates that rule still
    answers with the address that cannot be stale.
    """
    if cart.user is not None and cart.user.email:
        return cart.user.email, "account"
    if cart.guest_email:
        return cart.guest_email, "checkout"
    return None, None


#: When a basket was last touched, with `updated_at` as the fallback.
#:
#: The fallback covers rows written before `last_activity_at` existed and any
#: the backfill in migration 116 could not reach. Written once and used in the
#: filter, the sort and the response, so a basket cannot be selected by one
#: clock and then displayed against another.
_ACTIVITY = func.coalesce(Cart.last_activity_at, Cart.updated_at)


def live_carts_query(
    now: datetime,
    *,
    search: str | None = None,
    has_email: bool | None = None,
    idle_minutes_min: int = 0,
    idle_days_max: int = 30,
):
    """
    The baskets holding items, newest activity first, as a statement.

    Its own function so a test can compile it against PostgreSQL. Everything
    here — a partial index's `NULLS LAST`, an `EXISTS` over `cart_items`, a
    `contains_eager` hung off an outer join — is the kind of thing that is fine
    in Python and wrong in SQL, and a handler that builds its query inline can
    only be checked against a database.

    `min_value` is deliberately **not** here. A line's price is the product's
    base plus a JSONB snapshot whose free-allowance rule lives in
    `cart_service.option_charge`; expressing that in SQL would be a second
    implementation of money math this module promises not to write. It is
    applied to the loaded rows instead, over a set bounded by `idle_days_max`.
    """
    base = (
        select(Cart)
        .outerjoin(User, User.id == Cart.user_id)
        .where(
            # "Current carts *with items*". An empty basket is a row the
            # storefront made on a page load, not a shopper who chose anything.
            Cart.items.any(),
            _ACTIVITY >= now - timedelta(days=idle_days_max),
            _ACTIVITY <= now - timedelta(minutes=idle_minutes_min),
        )
    )

    if has_email is True:
        base = base.where(or_(User.email.isnot(None), Cart.guest_email.isnot(None)))
    elif has_email is False:
        base = base.where(User.email.is_(None), Cart.guest_email.is_(None))

    if search:
        term = search.strip()
        base = base.where(
            or_(
                search_text.contains(User.email, term),
                search_text.contains(Cart.guest_email, term),
                search_text.contains(Cart.session_id, term),
            )
        )

    return base.order_by(_ACTIVITY.desc().nullslast(), Cart.id.desc()).options(
        # `contains_eager`, not `joinedload`: the outer join to `users` is
        # already in the statement because the filters above read `User.email`,
        # and a `joinedload` would add a second, aliased copy of the same join
        # to populate `cart.user` from.
        contains_eager(Cart.user),
        selectinload(Cart.items).joinedload(CartItem.product),
    )


@router.get("/live-carts", response_model=LiveCartsResponse)
async def get_live_carts(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    search: str | None = Query(
        None, description="Match an email address or a session id"
    ),
    has_email: bool | None = Query(
        None, description="Only baskets we can (or cannot) write to"
    ),
    min_value: float | None = Query(
        None, ge=0, description="Only baskets worth at least this, in goods"
    ),
    idle_minutes_min: int = Query(
        0, ge=0, description="Only baskets untouched for at least this long"
    ),
    idle_days_max: int = Query(
        30,
        ge=1,
        le=365,
        description="Ignore baskets untouched for longer than this",
    ),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("customers.read")),
):
    """
    Every basket that currently holds items, most recently touched first.

    What the shop could not see before this endpoint: that eleven people are
    holding four thousand dirhams of cake right now, that six of them stopped
    two hours ago, and that four of those six left an address on the way past
    the checkout. That last number is the abandoned-cart business case, and
    `summary` states it directly.

    **Every figure is the server's.** The subtotal is
    `cart_service.line_total` — the same formula the storefront basket renders
    from — and the surcharge is `order_pricing.low_order_fee_for`, the function
    that charges it. There is no arithmetic on the console side and no second
    copy of either rule here (CLAUDE.md rule 10).

    **The delivery fee is a quote, not a price.** It is what a courier said this
    basket would cost to the pin the shopper had dropped, captured by the
    checkout preview while they were still deciding. A basket that never reached
    an address shows nothing rather than a guess.

    `idle_days_max` is a floor under the query rather than a preference: `carts`
    has no expiry, so without it this grows to every basket ever abandoned and
    the page nobody wants is the one the database works hardest for.
    """
    now = datetime.now(timezone.utc)
    stmt = live_carts_query(
        now,
        search=search,
        has_email=has_email,
        idle_minutes_min=idle_minutes_min,
        idle_days_max=idle_days_max,
    )
    carts = (await db.execute(stmt)).unique().scalars().all()

    settings_row = await delivery_service.get_settings(db)

    rows: list[LiveCart] = []
    for cart in carts:
        subtotal = cart_service.subtotal_of(cart)
        if min_value is not None and subtotal < Decimal(str(min_value)):
            continue

        email, email_source = _cart_email(cart)
        last_activity = cart.last_activity_at or cart.updated_at
        idle_minutes = (
            int((now - last_activity).total_seconds() // 60)
            if last_activity is not None and last_activity.tzinfo is not None
            else None
        )

        low_order_fee = order_pricing.low_order_fee_for(
            subtotal, DeliveryMethodEnum.DELIVERY, settings_row
        )
        delivery_fee = cart.delivery_quote_fee

        rows.append(
            LiveCart(
                id=cart.id,
                user_id=cart.user_id,
                session_id=cart.session_id,
                email=email,
                email_source=email_source,
                is_registered=cart.user is not None and not cart.user.is_guest,
                lines=[
                    LiveCartLine(
                        product_id=item.product_id,
                        product_name=(
                            item.product.name if item.product else "(deleted product)"
                        ),
                        product_sku=item.product.sku if item.product else None,
                        quantity=item.quantity,
                        unit_price=float(cart_service.line_unit_price(item)),
                        line_total=float(cart_service.line_total(item)),
                        options=[
                            _option_words(opt) for opt in (item.selected_options or [])
                        ],
                        personalisation_note=item.personalisation_note,
                    )
                    for item in cart.items
                ],
                item_count=sum(item.quantity for item in cart.items),
                subtotal=float(subtotal),
                low_order_fee=float(low_order_fee),
                delivery_fee=float(delivery_fee) if delivery_fee is not None else None,
                delivery_zone=cart.delivery_quote_zone,
                estimated_total=float(
                    subtotal + low_order_fee + (delivery_fee or Decimal("0.00"))
                ),
                promo_code=cart.promo_code,
                created_at=cart.created_at,
                last_activity_at=last_activity,
                idle_minutes=idle_minutes,
            )
        )

    # Over the whole filtered set, not the page — a header that changed as you
    # paged through it would be answering a question nobody asked.
    summary = LiveCartsSummary(
        carts=len(rows),
        total_value=round(sum(row.subtotal for row in rows), 2),
        with_email=sum(1 for row in rows if row.email),
        reachable_value=round(sum(row.subtotal for row in rows if row.email), 2),
        idle_over_1h=sum(
            1 for row in rows if row.idle_minutes is not None and row.idle_minutes >= 60
        ),
        idle_over_24h=sum(
            1
            for row in rows
            if row.idle_minutes is not None and row.idle_minutes >= 1440
        ),
    )

    total = len(rows)
    start = (page - 1) * per_page
    return LiveCartsResponse(
        items=rows[start : start + per_page],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
        summary=summary,
    )
