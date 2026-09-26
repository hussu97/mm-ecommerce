"""
Custom orders on the register — the "Customized Cake Orders" section.

Only at the custom-orders branch (every route checks the signed-in user may act
there). A register takes an order, claims and prints its kitchen docket, fills
in the customer and address, marks it packed or collected, or cancels it.
Choosing a courier, recording a third-party one and the invoice are the
console's alone and are not here. Custom-cake production is raised from `operations` beside the rest of
production (`POST /pos/inventory/production/custom-cake`).

Addressed by order id: the register holds ids, as it does for every order.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.schemas.custom_order import (
    CustomCakeItem,
    CustomOrderClaimResult,
    CustomOrderContactUpdate,
    CustomOrderCreate,
    CustomOrderListItem,
    CustomOrderRecipeUpdate,
    CustomOrderResponse,
    CustomOrderUpdate,
)
from app.services.inventory import access_service
from app.services.orders import custom_order_service

router = APIRouter()

_MANAGE = "orders.custom.manage"


async def _allowed(db: AsyncSession, user: User) -> None:
    cfg = await custom_order_service.config(db)
    await access_service.assert_branch_access(db, user, cfg.branch.id)


async def _respond(db: AsyncSession, order_id: uuid.UUID) -> CustomOrderResponse:
    order, custom = await custom_order_service.get_by_id(db, order_id)
    return await custom_order_service.to_response(db, order, custom)


@router.get("", response_model=list[CustomOrderListItem])
async def list_open_custom_orders(
    status_group: str = Query("pending", pattern="^(pending|packed)$"),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """The kitchen's open custom orders: still to make (`pending`) or boxed and
    waiting to leave (`packed`, which includes a failed hand-over)."""
    await _allowed(db, user)
    rows, _ = await custom_order_service.list_orders(
        db, status_group=status_group, q=q, page=1, per_page=limit
    )
    return await custom_order_service.to_list_items(db, rows)


@router.get("/unprinted", response_model=list[CustomOrderResponse])
async def unprinted_custom_orders(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.till.manage")),
):
    """Orders whose kitchen docket no register has claimed — swept on till open
    and when the section opens, for a push that arrived while the iPad slept."""
    await _allowed(db, user)
    return [
        await _respond(db, order.id)
        for order in await custom_order_service.unprinted(db)
    ]


@router.get("/items", response_model=list[CustomCakeItem])
async def custom_cake_items(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """What a recipe may use and the register may produce, with stock."""
    await _allowed(db, user)
    return [
        custom_order_service.cake_item_out(item, on_hand)
        for item, on_hand in await custom_order_service.custom_cake_items(db)
    ]


@router.post(
    "", response_model=CustomOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_custom_order(
    data: CustomOrderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """Take a custom order at the register. The register then claims and prints
    its docket itself; no push is sent."""
    await _allowed(db, user)
    order = await custom_order_service.create(db, data, user=user, via="pos")
    return await _respond(db, order.id)


@router.get("/{order_id}", response_model=CustomOrderResponse)
async def get_custom_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    await _allowed(db, user)
    return await _respond(db, order_id)


@router.put("/{order_id}", response_model=CustomOrderResponse)
async def update_custom_order(
    order_id: uuid.UUID,
    data: CustomOrderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    await _allowed(db, user)
    order, custom = await custom_order_service.get_by_id(db, order_id)
    await custom_order_service.update(db, order, custom, data, user=user)
    return await _respond(db, order_id)


@router.put("/{order_id}/contact", response_model=CustomOrderResponse)
async def update_custom_order_contact(
    order_id: uuid.UUID,
    data: CustomOrderContactUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    await _allowed(db, user)
    order, _ = await custom_order_service.get_by_id(db, order_id)
    await custom_order_service.update_contact(
        db, order, customer=data.customer, address=data.address
    )
    return await _respond(db, order_id)


@router.put("/{order_id}/recipe", response_model=CustomOrderResponse)
async def update_custom_order_recipe(
    order_id: uuid.UUID,
    data: CustomOrderRecipeUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    await _allowed(db, user)
    order, custom = await custom_order_service.get_by_id(db, order_id)
    await custom_order_service.update_recipe(db, order, custom, data.recipe)
    return await _respond(db, order_id)


@router.post("/{order_id}/claim-print", response_model=CustomOrderClaimResult)
async def claim_custom_order_docket(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.till.manage")),
):
    """Claim the kitchen docket. Only the register told `claimed: true` prints;
    the order is then at the POS. A reprint needs no claim."""
    await _allowed(db, user)
    order, _ = await custom_order_service.get_by_id(db, order_id)
    claimed = await custom_order_service.claim_print(db, order, user=user)
    return CustomOrderClaimResult(claimed=claimed, order=await _respond(db, order_id))


@router.post("/{order_id}/pack", response_model=CustomOrderResponse)
async def pack_custom_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """Boxed and ready: the recipe is consumed from the kitchen's stock."""
    await _allowed(db, user)
    order, _ = await custom_order_service.get_by_id(db, order_id)
    await custom_order_service.pack(db, order, user=user, via="pos")
    return await _respond(db, order_id)


@router.post("/{order_id}/collected", response_model=CustomOrderResponse)
async def mark_custom_order_collected(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """The customer took it from the counter."""
    await _allowed(db, user)
    order, _ = await custom_order_service.get_by_id(db, order_id)
    await custom_order_service.mark_collected(db, order, user=user, via="pos")
    return await _respond(db, order_id)


@router.post("/{order_id}/cancel", response_model=CustomOrderResponse)
async def cancel_custom_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require(_MANAGE)),
):
    """Call the order off. Offered while `actions.can_cancel` — before it is
    packed, or packed and not yet handed over; what packing consumed stays
    consumed."""
    await _allowed(db, user)
    order, _ = await custom_order_service.get_by_id(db, order_id)
    await custom_order_service.cancel(db, order, user=user, via="pos")
    return await _respond(db, order_id)
