"""
What a branch can actually make right now.

One definition, because four callers ask it and a disagreement between any two
of them is a customer paying for something the shop cannot hand over: the
storefront catalogue, the cart, order placement, and the terminal's own screen.

Three ideas, and they are separate on purpose.

**A row is an exception.** `branch_products` and `branch_modifier_options` hold
a row only where a branch differs from the catalogue, so "no row" means "sold
here" and the ordinary case costs nothing to store or read.

**Out-of-stock expires on read.** `out_of_stock_until` is compared with now
every time the question is asked. A branch that marked kunafa out until close
is selling it again the moment the clock passes, whether or not any job has
run since — so a missed sweep cannot leave a shop unable to sell. `sweep()`
deletes lapsed rows to keep the tables small; it is housekeeping, and deleting
it would change nothing a customer can see.

**A product needs its required groups.** A box of three is unsellable when
every filling is gone, and perfectly sellable when one remains. So the product
question is not only "is the product marked out" but "does every group that
*must* be chosen from still have something to choose". An optional group with
nothing left is not a blocker — nobody had to pick from it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.branch import Branch
from app.models.delivery_polygon import DeliveryPolygon, DeliveryPolygonVersion
from app.models.menu import BranchModifierOption, BranchProduct
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.polygon_branch_fulfilment import PolygonBranchFulfilment
from app.models.product import Product
from app.services import option_snapshot
from app.services.pos import business_day_service

#: How long "for an hour" is. Named because it is a shop's decision rather
#: than an obvious constant, and the terminal shows it as a choice.
ONE_HOUR = timedelta(hours=1)

#: The durations the terminal offers. `indefinite` is the absence of a moment,
#: not a very large one — "until somebody says so" has no clock.
DURATION_INDEFINITE = "indefinite"
DURATION_END_OF_DAY = "end_of_day"
DURATION_ONE_HOUR = "one_hour"
DURATIONS: tuple[str, ...] = (
    DURATION_INDEFINITE,
    DURATION_END_OF_DAY,
    DURATION_ONE_HOUR,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── When it comes back ───────────────────────────────────────────────────────


async def resolve_until(
    db: AsyncSession, *, branch: Branch, duration: str, now: datetime | None = None
) -> datetime | None:
    """
    Turn one of `DURATIONS` into the moment stock returns.

    `end_of_day` is the branch's *own* rollover, not midnight: a shop trading
    to 2am with a 04:00 cutoff is still working the same day, and an item
    marked out at 1am must not return an hour later while the same staff are
    still standing there. This is the rollover `business_day_service` files
    orders under, so "back tomorrow" means the same thing to the books and to
    the menu.
    """
    if duration == DURATION_INDEFINITE:
        return None

    moment = now or _now()
    if duration == DURATION_ONE_HOUR:
        return moment + ONE_HOUR

    if duration == DURATION_END_OF_DAY:
        tz = await business_day_service.resolve_timezone(db)
        return business_day_service.next_rollover(branch, moment, tz)

    raise ValueError(f"Unknown duration {duration!r}")


# ─── The predicate, in SQL ────────────────────────────────────────────────────


def _lapsed(column):
    """`out_of_stock_until` has passed, so the row no longer bites."""
    return and_(column.isnot(None), column <= func.now())


def unavailable_product_ids_at(branch_id: uuid.UUID):
    """Products explicitly marked out at this branch, expiry applied."""
    return select(BranchProduct.product_id).where(
        BranchProduct.branch_id == branch_id,
        or_(
            BranchProduct.is_active.is_(False),
            and_(
                BranchProduct.is_in_stock.is_(False),
                ~_lapsed(BranchProduct.out_of_stock_until),
            ),
        ),
    )


def unavailable_option_ids_at(branch_id: uuid.UUID):
    """Modifier options explicitly marked out at this branch, expiry applied."""
    return select(BranchModifierOption.modifier_option_id).where(
        BranchModifierOption.branch_id == branch_id,
        BranchModifierOption.is_in_stock.is_(False),
        ~_lapsed(BranchModifierOption.out_of_stock_until),
    )


def unsellable_at_branch_subquery(branch_id: uuid.UUID):
    """
    Products this one branch cannot make, as a correlated predicate on `Product`.

    The SQL twin of `BranchAvailability.product_available`, and it has to stay
    the twin: this decides what a shopper is shown, that decides what the cart
    and the checkout refuse, and a shopper offered a cake the checkout will not
    take has been sent down a corridor with a wall at the end.

    Both halves of the rule, for the same reason the in-memory one has both. A
    product marked out is unsellable. So is a product nobody marked whose
    *required* groups have nothing left to choose from — a box of three with no
    fillings is not a box. An optional group emptying out is not a blocker,
    because nobody had to pick from it, which is why this reads
    `minimum_options >= 1` rather than any flag named "required".

    Live options only: an option switched off in the catalogue is gone
    everywhere, and counting it as something left to choose would keep a box
    sellable on the strength of a filling no branch has.
    """
    marked_out = Product.id.in_(unavailable_product_ids_at(branch_id))

    out_here = unavailable_option_ids_at(branch_id)
    # A required group with nothing left in it. `~exists` on the options rather
    # than a count, so Postgres can stop at the first live one.
    empty_required_group = (
        select(ProductModifier.id)
        .join(Modifier, Modifier.id == ProductModifier.modifier_id)
        .where(
            ProductModifier.product_id == Product.id,
            ProductModifier.minimum_options >= 1,
            Modifier.is_active.is_(True),
            ~select(ModifierOption.id)
            .where(
                ModifierOption.modifier_id == Modifier.id,
                ModifierOption.is_active.is_(True),
                ModifierOption.id.notin_(out_here),
            )
            .exists(),
        )
        .correlate(Product)
        .exists()
    )

    return or_(marked_out, empty_required_group)


def _assigned_branch_ids_on_active_map():
    """Branches that actually serve at least one zone on the active map.

    A branch is only part of website delivery once someone has wired it into the
    map — given it a rank and a courier in some polygon (`polygon_branch_fulfilment`
    on the active version). The flag alone is not enough: a branch switched on for
    online orders but never placed on the map has no zone it can serve, no courier
    to carry from it, and no business gating the catalogue.
    """
    return (
        select(PolygonBranchFulfilment.branch_id)
        .join(
            DeliveryPolygon,
            DeliveryPolygon.id == PolygonBranchFulfilment.polygon_id,
        )
        .join(
            DeliveryPolygonVersion,
            DeliveryPolygonVersion.id == DeliveryPolygon.version_id,
        )
        .where(DeliveryPolygonVersion.is_active.is_(True))
    )


def _website_delivery_branch_ids():
    """The branches the website union is taken over.

    A branch shows its shelf on the storefront only when it is active, bakes
    website orders (`receives_online_orders`), **and** is actually assigned to
    serve a zone on the active map (a branch priority + courier set in
    `polygon_branch_fulfilment`). The flag is necessary but not sufficient: a
    branch nobody has placed on the map serves no pin, so its stockouts must not
    gate what the website lists — and a counter-only shop (Barsha's till, say),
    which the flag already excludes, never appears at all.
    """
    return select(Branch.id).where(
        Branch.is_active.is_(True),
        Branch.receives_online_orders.is_(True),
        Branch.id.in_(_assigned_branch_ids_on_active_map()),
    )


def _out_at_every_of(branch_ids_select):
    """Products out at *every* branch in the given id-select — the count trick.

    Counting rows works because the tables are exception-only: being out at all
    N branches takes N rows, and no other state produces that many. `> 0` guards
    the empty set — zero branches is "we have no shops here", not "out
    everywhere", and hiding the whole catalogue over it is the worse answer.
    """
    branch_count = (
        select(func.count()).select_from(branch_ids_select.subquery()).scalar_subquery()
    )
    out_count = (
        select(func.count(func.distinct(BranchProduct.branch_id)))
        .where(
            BranchProduct.product_id == Product.id,
            BranchProduct.branch_id.in_(branch_ids_select),
            or_(
                BranchProduct.is_active.is_(False),
                and_(
                    BranchProduct.is_in_stock.is_(False),
                    ~_lapsed(BranchProduct.out_of_stock_until),
                ),
            ),
        )
        .correlate(Product)
        .scalar_subquery()
    )
    return and_(branch_count > 0, out_count >= branch_count)


def out_at_every_branch_subquery():
    """
    Products no website branch can make — the union catalogue's hide rule.

    The catalogue has no address and therefore no one branch, so it hides a
    product only when *every* website-delivery branch is out of it — anything
    less would hide a cake from a shopper a branch could still make it for. This
    is the union the storefront shows: the moment one website branch has it, it
    is listed. The per-branch answer is enforced later, at the cart and again at
    placement, where the basket picks the branch that can make the whole thing.
    """
    return _out_at_every_of(_website_delivery_branch_ids())


def out_at_every_branch_in_set_subquery(branch_ids: "Sequence[uuid.UUID]"):
    """
    Products out at every branch in a specific set — the per-pin narrowing.

    Once a pin resolves to a polygon, the branches that can serve it are known
    (the polygon's priority list). Narrowing the union to that set hides a
    product the shopper's own polygon cannot make it for, which is a truer
    answer than the whole-country union — while staying the union *within* the
    set, so a product one serving branch has is still shown. An empty set means
    "no branch serves this pin", which the caller handles as unserviceable
    rather than by hiding the catalogue, so this returns a false predicate.
    """
    if not branch_ids:
        return literal(False)
    ids = select(Branch.id).where(
        Branch.is_active.is_(True),
        Branch.id.in_(list(branch_ids)),
    )
    return _out_at_every_of(ids)


# ─── The predicate, in memory ─────────────────────────────────────────────────


@dataclass(frozen=True)
class BranchAvailability:
    """
    What one branch cannot make, as two sets of ids.

    Loaded once and asked many times — a cart of nine lines is nine questions
    about the same branch, and each of them is a set lookup rather than a query.
    """

    branch_id: uuid.UUID
    unavailable_product_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    unavailable_option_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def option_available(self, option_id: uuid.UUID) -> bool:
        return option_id not in self.unavailable_option_ids

    def product_available(self, product: Product) -> bool:
        """
        Whether this branch can make the product at all.

        Marked-out first, then the required groups: a product nobody marked is
        still unsellable when a group that has to be chosen from has nothing
        left in it.
        """
        if product.id in self.unavailable_product_ids:
            return False
        return not self.blocking_groups(product)

    def blocking_groups(self, product: Product) -> list[ProductModifier]:
        """
        Required groups this branch has nothing left in.

        Returned rather than counted so the caller can say *which* — "no
        fillings left" is a sentence somebody can act on, and "unavailable" is
        not. Needs `product_modifiers.modifier.options` loaded.
        """
        blocking: list[ProductModifier] = []
        for link in product.product_modifiers or []:
            if link.minimum_options < 1:
                continue  # nobody has to choose from it
            modifier = link.modifier
            if modifier is None or not modifier.is_active:
                continue
            live = [
                option
                for option in (modifier.options or [])
                if option.is_active and self.option_available(option.id)
            ]
            if not live:
                blocking.append(link)
        return blocking


def available_at_any(
    product: Product, availabilities: "Sequence[BranchAvailability]"
) -> bool:
    """Whether *some* branch in the set can make the product — the union rule.

    The in-memory twin of `out_at_every_branch_in_set_subquery`: the catalogue
    lists a product the moment one serving branch has it, and only the cart,
    which knows the whole basket, narrows to a single branch. Empty set → False:
    no branch, nothing to make it.
    """
    return any(a.product_available(product) for a in availabilities)


#: The refusal a checkout can branch on. The message is free to be reworded;
#: this is the promise the web app reads to decide whether to open the
#: resolution screen instead of showing a toast.
UNAVAILABLE_AT_BRANCH = "items_unavailable_at_branch"


@dataclass(frozen=True)
class UnavailableLine:
    """One cart line the resolved branch cannot make, and why."""

    product_id: uuid.UUID
    product_name: str
    #: Names of the specific options that are out, where that is the cause.
    #: Empty when the product itself is out — the two read differently to a
    #: customer, who can swap a filling but not conjure a cake.
    unavailable_option_names: tuple[str, ...] = ()
    #: Names of required groups the branch has nothing left in.
    empty_group_names: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        """The sentence shown beside the line."""
        if self.unavailable_option_names:
            return f"{', '.join(self.unavailable_option_names)} not available today"
        if self.empty_group_names:
            return f"No {', '.join(self.empty_group_names).lower()} left today"
        return "Not available at the branch serving this address"


def line_unavailability(
    availability: BranchAvailability,
    *,
    product: Product,
    selected_options: list[dict] | None,
) -> UnavailableLine | None:
    """
    Whether one basket line can be made, and what to say when it cannot.

    Checks the options the customer actually chose rather than the whole
    catalogue: a box with three fillings picked is blocked by *those* fillings
    running out, and is unaffected by a fourth the customer did not want.

    Snapshots are read through `option_snapshot`, which is the one place that
    knows the column has two dialects — a website basket writes `option_id`
    and a counter sale `modifier_option_id`, and reading either by hand here
    would be the third spelling.
    """
    blocked_options: list[str] = []
    for row in option_snapshot.for_register(selected_options or []):
        raw_id = row.get("modifier_option_id")
        if not raw_id:
            continue
        try:
            option_id = uuid.UUID(str(raw_id))
        except (ValueError, AttributeError, TypeError):
            # Rows predating UUID ids. Nothing can be looked up, and refusing
            # the basket over an unreadable historic snapshot would block a
            # sale to protect a check that cannot run.
            continue
        if not availability.option_available(option_id):
            blocked_options.append(str(row.get("name") or "").strip())

    if blocked_options:
        return UnavailableLine(
            product_id=product.id,
            product_name=product.name,
            unavailable_option_names=tuple(name for name in blocked_options if name),
        )

    if product.id in availability.unavailable_product_ids:
        return UnavailableLine(product_id=product.id, product_name=product.name)

    blocking = availability.blocking_groups(product)
    if blocking:
        return UnavailableLine(
            product_id=product.id,
            product_name=product.name,
            empty_group_names=tuple(
                link.modifier.name for link in blocking if link.modifier
            ),
        )
    return None


async def load_for_branch(
    db: AsyncSession, branch_id: uuid.UUID | None
) -> BranchAvailability:
    """Everything one branch is out of, in two queries."""
    if branch_id is None:
        # No address yet, so no branch to be out of anything. The catalogue's
        # own hide-everywhere rule still applies; this is the per-branch layer
        # and it has nothing to say until a branch is known.
        return BranchAvailability(branch_id=uuid.UUID(int=0))

    products = await db.execute(unavailable_product_ids_at(branch_id))
    options = await db.execute(unavailable_option_ids_at(branch_id))
    return BranchAvailability(
        branch_id=branch_id,
        unavailable_product_ids=frozenset(products.scalars().all()),
        unavailable_option_ids=frozenset(options.scalars().all()),
    )


async def unavailable_cart_lines(
    db: AsyncSession, *, cart, branch_id: uuid.UUID | None
) -> list[UnavailableLine]:
    """
    Every line in this basket the named branch cannot make.

    The one answer behind both halves of the checkout: the resolution screen
    asks it to show the customer what to change, and `create_order` asks it
    again at the button so a screen that was skipped, stale or bypassed cannot
    put through a sale nobody can fill.
    """
    if branch_id is None:
        return []
    availability = await load_for_branch(db, branch_id)
    if (
        not availability.unavailable_product_ids
        and not availability.unavailable_option_ids
    ):
        # Nothing is out at this branch, so no line can be blocked and the
        # per-line loop — which needs modifier groups loaded — is skipped
        # entirely. This is the ordinary case on an ordinary day.
        return []

    blocked: list[UnavailableLine] = []
    for item in cart.items:
        if item.product is None:
            continue
        found = line_unavailability(
            availability,
            product=item.product,
            selected_options=item.selected_options,
        )
        if found is not None:
            blocked.append(found)
    return blocked


def products_load_options():
    """Eager loads `blocking_groups` needs, so it cannot lazy-load mid-request."""
    return [_blocking_groups_chain(selectinload(Product.product_modifiers))]


def cart_load_options():
    """
    The same requirement, rooted at a `Cart` instead of a `Product`.

    Exists because the requirement had exactly one spelling and the checkout
    could not use it. `blocking_groups` walks `product_modifiers → modifier →
    options`, `products_load_options` said so for a query selecting products,
    and the two callers that reach it through a *cart* — `preview_order` and
    `create_order` — loaded their products for pricing instead and lazy-loaded
    the rest. Under async SQLAlchemy that is not a slow query, it is a
    `MissingGreenlet`, and it took the checkout down with a 500.

    It only ever fired at a branch with something actually out of stock, since
    `unavailable_cart_lines` returns before the per-line loop otherwise. So it
    lay dormant through every test and every ordinary day, and surfaced as
    "checkout is broken" on exactly the days the shop had run out of something.

    Both spellings live here, next to the function whose needs they describe,
    so a change to `blocking_groups` updates one file rather than however many
    callers happened to know.
    """
    from app.models.cart import Cart, CartItem

    return [
        _blocking_groups_chain(
            selectinload(Cart.items)
            .joinedload(CartItem.product)
            .selectinload(Product.product_modifiers)
        )
    ]


def _blocking_groups_chain(loader):
    """`product_modifiers → modifier → options`, onto whatever precedes it."""
    return loader.selectinload(ProductModifier.modifier).selectinload(Modifier.options)


# ─── Housekeeping ─────────────────────────────────────────────────────────────


async def sweep(db: AsyncSession) -> int:
    """
    Delete rows whose moment has passed.

    Nothing depends on this having run — every reader applies the expiry itself
    — so it is safe to skip, safe to run twice, and safe to run late. It exists
    so the tables stay the size of what is actually out rather than growing a
    row for every stockout the shop has ever had.
    """
    now = _now()
    removed = 0
    for model in (BranchProduct, BranchModifierOption):
        # `branch_products` also carries price and is_active overrides, so a
        # lapsed row there is only disposable when it says nothing else.
        conditions = [
            model.is_in_stock.is_(False),
            model.out_of_stock_until.isnot(None),
            model.out_of_stock_until <= now,
        ]
        if model is BranchProduct:
            conditions += [
                BranchProduct.price.is_(None),
                BranchProduct.is_active.is_(True),
            ]
        result = await db.execute(delete(model).where(*conditions))
        removed += result.rowcount or 0
    await db.flush()
    return removed


# ─── Writing ──────────────────────────────────────────────────────────────────


async def set_product_stock(
    db: AsyncSession,
    *,
    branch: Branch,
    product_id: uuid.UUID,
    in_stock: bool,
    duration: str = DURATION_INDEFINITE,
) -> BranchProduct:
    """Mark one product out at one branch, or put it back."""
    row = (
        await db.execute(
            select(BranchProduct).where(
                BranchProduct.branch_id == branch.id,
                BranchProduct.product_id == product_id,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = BranchProduct(branch_id=branch.id, product_id=product_id)
        db.add(row)

    row.is_in_stock = in_stock
    # Putting something back clears the clock as well as the flag; the CHECK
    # refuses a row that is available and still counting down.
    row.out_of_stock_until = (
        None if in_stock else await resolve_until(db, branch=branch, duration=duration)
    )
    await db.flush()
    return row


async def set_option_stock(
    db: AsyncSession,
    *,
    branch: Branch,
    option_id: uuid.UUID,
    in_stock: bool,
    duration: str = DURATION_INDEFINITE,
) -> BranchModifierOption:
    """Mark one modifier option out at one branch, or put it back."""
    row = (
        await db.execute(
            select(BranchModifierOption).where(
                BranchModifierOption.branch_id == branch.id,
                BranchModifierOption.modifier_option_id == option_id,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = BranchModifierOption(branch_id=branch.id, modifier_option_id=option_id)
        db.add(row)

    row.is_in_stock = in_stock
    row.out_of_stock_until = (
        None if in_stock else await resolve_until(db, branch=branch, duration=duration)
    )
    await db.flush()
    return row


async def set_all_options_stock(
    db: AsyncSession,
    *,
    branch: Branch,
    product_id: uuid.UUID,
    in_stock: bool,
    duration: str = DURATION_INDEFINITE,
) -> int:
    """
    Every option on one product, in one go.

    The terminal's "all of them" control. A counter that has run out of one
    ingredient across every filling should press one button, not eleven.
    """
    option_ids = (
        (
            await db.execute(
                select(ModifierOption.id)
                .join(Modifier, Modifier.id == ModifierOption.modifier_id)
                .join(ProductModifier, ProductModifier.modifier_id == Modifier.id)
                .where(ProductModifier.product_id == product_id)
            )
        )
        .scalars()
        .all()
    )
    for option_id in option_ids:
        await set_option_stock(
            db,
            branch=branch,
            option_id=option_id,
            in_stock=in_stock,
            duration=duration,
        )
    return len(option_ids)
