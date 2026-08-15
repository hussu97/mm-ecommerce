"""
"86 it" — the website menu, as the counter sees it.

Deliberately a screen of its own rather than a control on the register's
product grid. The two lists are not the same list: the grid is what this shop
sells over the counter, and this is what the *website* offers, which includes
boxes the counter never rings up and excludes the coffee it sells all day. A
cashier marking pistachio out is answering a question about the website, and
mixing that into the till would mean marking things out that were never on it.

Everything here is scoped to the terminal's own branch. A device token names one
shop; being able to close another shop's menu from this counter is not a
feature.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.permissions import require
from app.core.exceptions import NotFoundError
from app.models.branch import Branch
from app.models.device import Device
from app.models.menu import BranchModifierOption, BranchProduct
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import WEB_CHANNEL, Product, sells_on
from app.models.user import User
from app.services import availability_service

from .devices import get_current_device

router = APIRouter()


# ─── Wire shapes ──────────────────────────────────────────────────────────────


class OptionAvailability(BaseModel):
    id: uuid.UUID
    name: str
    modifier_id: uuid.UUID
    modifier_name: str
    is_in_stock: bool
    #: Null when it is out until somebody says otherwise.
    out_of_stock_until: str | None = None


class ProductAvailability(BaseModel):
    """One product on the website menu, and what this branch has left of it."""

    id: uuid.UUID
    name: str
    category_name: str | None = None
    image_url: str | None = None
    is_in_stock: bool
    out_of_stock_until: str | None = None
    #: Empty for a product with no modifiers, which is the whole reason the
    #: terminal can mark those out with one tap and no sheet.
    options: list[OptionAvailability] = Field(default_factory=list)
    #: Whether the website can currently sell it at this branch at all — false
    #: when the product is out, or when a group that must be chosen from has
    #: nothing left. Computed here so the screen and the storefront cannot
    #: disagree about what "unavailable" means.
    is_sellable: bool = True


class SetStockRequest(BaseModel):
    in_stock: bool
    #: One of `availability_service.DURATIONS`. Ignored when putting something
    #: back — returning stock has no clock.
    duration: str = availability_service.DURATION_INDEFINITE
    #: Products with modifiers only: mark every option out as well, which is
    #: what "the whole thing is off" means for a build-your-own box.
    include_options: bool = False


def _iso(value) -> str | None:
    return value.isoformat() if value else None


async def _branch_of(db: AsyncSession, device: Device) -> Branch:
    """The shop this terminal stands in. Never another one."""
    branch = await db.get(Branch, device.branch_id)
    if branch is None:
        raise NotFoundError("This terminal is not assigned to a branch")
    return branch


async def _rows(
    db: AsyncSession, device: Device, product_ids: list[uuid.UUID] | None = None
) -> list[ProductAvailability]:
    """
    The website menu for this branch, or just the products named.

    One builder for the list screen and for the reply to a change, so a row the
    terminal renders after marking something out is assembled exactly like the
    row it rendered before — `is_sellable` in particular, which the screen uses
    to grey a whole product out and which no client should be deriving.
    """
    stmt = (
        select(Product)
        .where(Product.is_active.is_(True), sells_on(WEB_CHANNEL))
        .options(
            selectinload(Product.category),
            *availability_service.products_load_options(),
        )
        .order_by(Product.display_order, Product.name)
    )
    if product_ids is not None:
        stmt = stmt.where(Product.id.in_(product_ids))

    products = (await db.execute(stmt)).scalars().unique().all()
    availability = await availability_service.load_for_branch(db, device.branch_id)

    # The rows themselves, for the return time. `load_for_branch` answers
    # "is it out", which is all the storefront needs; a counter also wants to
    # read "back at six" off the screen.
    product_rows = {
        row.product_id: row
        for row in (
            await db.execute(
                select(BranchProduct).where(BranchProduct.branch_id == device.branch_id)
            )
        )
        .scalars()
        .all()
    }
    option_rows = {
        row.modifier_option_id: row
        for row in (
            await db.execute(
                select(BranchModifierOption).where(
                    BranchModifierOption.branch_id == device.branch_id
                )
            )
        )
        .scalars()
        .all()
    }

    result: list[ProductAvailability] = []
    for product in products:
        options: list[OptionAvailability] = []
        for link in sorted(
            product.product_modifiers or [], key=lambda pm: pm.display_order
        ):
            modifier = link.modifier
            if modifier is None or not modifier.is_active:
                continue
            for option in sorted(modifier.options or [], key=lambda o: o.display_order):
                if not option.is_active:
                    continue
                row = option_rows.get(option.id)
                options.append(
                    OptionAvailability(
                        id=option.id,
                        name=option.name,
                        modifier_id=modifier.id,
                        modifier_name=modifier.name,
                        is_in_stock=availability.option_available(option.id),
                        out_of_stock_until=_iso(
                            row.out_of_stock_until if row else None
                        ),
                    )
                )

        row = product_rows.get(product.id)
        result.append(
            ProductAvailability(
                id=product.id,
                name=product.name,
                category_name=product.category.name if product.category else None,
                image_url=(product.image_urls or [None])[0],
                is_in_stock=product.id not in availability.unavailable_product_ids,
                out_of_stock_until=_iso(row.out_of_stock_until if row else None),
                options=options,
                is_sellable=availability.product_available(product),
            )
        )
    return result


async def _one(
    db: AsyncSession, device: Device, product_id: uuid.UUID
) -> ProductAvailability:
    rows = await _rows(db, device, [product_id])
    if not rows:
        raise NotFoundError("Product not found")
    return rows[0]


# ─── Reading ──────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ProductAvailability])
async def list_availability(
    db: AsyncSession = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> list[ProductAvailability]:
    """
    The website menu for this terminal's branch, with what is out marked.

    Reads the same `sells_on(WEB_CHANNEL)` the storefront lists from, so the
    screen cannot drift from the site: an item that is not on the website has
    no business being markable out of stock *on* the website.

    Inactive products are excluded — a de-listed cake is not "out of stock",
    and offering it here would invite somebody to put it back in a place that
    cannot.
    """
    return await _rows(db, device)


# ─── Writing ──────────────────────────────────────────────────────────────────


@router.put("/products/{product_id}", response_model=ProductAvailability)
async def set_product_availability(
    product_id: uuid.UUID,
    data: SetStockRequest,
    db: AsyncSession = Depends(get_db),
    device: Device = Depends(get_current_device),
    _: User = Depends(require("pos.products.availability")),
) -> ProductAvailability:
    """
    Mark one website product out at this branch, or put it back.

    `include_options` is what "all of it" means on a build-your-own box: the
    product itself *and* every filling, so the customer is not offered a choice
    of nothing. Putting it back with the same flag restores them together.
    """
    branch = await _branch_of(db, device)

    product = await db.get(Product, product_id)
    if product is None or not product.is_active:
        raise NotFoundError("Product not found")

    await availability_service.set_product_stock(
        db,
        branch=branch,
        product_id=product_id,
        in_stock=data.in_stock,
        duration=data.duration,
    )
    if data.include_options:
        await availability_service.set_all_options_stock(
            db,
            branch=branch,
            product_id=product_id,
            in_stock=data.in_stock,
            duration=data.duration,
        )

    return await _one(db, device, product_id)


@router.put("/options/{option_id}", response_model=ProductAvailability)
async def set_option_availability(
    option_id: uuid.UUID,
    data: SetStockRequest,
    db: AsyncSession = Depends(get_db),
    device: Device = Depends(get_current_device),
    _: User = Depends(require("pos.products.availability")),
) -> ProductAvailability:
    """
    Mark one filling out at this branch.

    Returns the *product* rather than the option, because the answer that
    matters is what the screen must now show: marking the last filling out
    makes the whole box unsellable, and a response describing only the option
    would leave the terminal to work that out for itself.
    """
    branch = await _branch_of(db, device)

    option = await db.get(ModifierOption, option_id)
    if option is None:
        raise NotFoundError("Option not found")

    await availability_service.set_option_stock(
        db,
        branch=branch,
        option_id=option_id,
        in_stock=data.in_stock,
        duration=data.duration,
    )

    product_id = (
        await db.execute(
            select(ProductModifier.product_id)
            .join(Modifier, Modifier.id == ProductModifier.modifier_id)
            .join(ModifierOption, ModifierOption.modifier_id == Modifier.id)
            .where(ModifierOption.id == option_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if product_id is None:
        raise NotFoundError("That option is not on any product")
    return await _one(db, device, product_id)
