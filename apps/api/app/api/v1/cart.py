from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_optional_user
from app.core.exceptions import UnauthorizedError
from app.models.user import User
from app.schemas.cart import (
    CartItemCreate,
    CartItemNoteUpdate,
    CartItemUpdate,
    CartResponse,
)
from app.services import cart_service

router = APIRouter()


class CartPromoRequest(BaseModel):
    """The code the basket has applied, or `null` to forget it."""

    code: str | None = None


class CartMergeRequest(BaseModel):
    session_id: str


def _resolve_identity(
    current_user: User | None,
    x_session_id: str | None,
) -> tuple[uuid.UUID | None, str | None]:
    """Return (user_id, session_id) from the request context."""
    if current_user:
        return current_user.id, None
    return None, x_session_id


@router.get("", response_model=CartResponse)
async def get_cart(
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Get or create cart for the current user or guest session."""
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.get_or_create(db, user_id=user_id, session_id=session_id)


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    data: CartItemCreate,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Add an item to the cart (or increase quantity if already present)."""
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.add_item(
        db, user_id=user_id, session_id=session_id, data=data
    )


@router.put("/items/{item_id}", response_model=CartResponse)
async def update_cart_item(
    item_id: uuid.UUID,
    data: CartItemUpdate,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Update quantity of a cart item."""
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.update_item(
        db, user_id=user_id, session_id=session_id, item_id=item_id, data=data
    )


@router.put("/items/{item_id}/note", response_model=CartResponse)
async def update_cart_item_note(
    item_id: uuid.UUID,
    data: CartItemNoteUpdate,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    Set the personalised message on a cart item.

    Its own route rather than a field on the quantity update, because the note
    is saved as the customer types and a debounced save that also carried a
    quantity would eventually overwrite a stepper change made in the same
    moment.
    """
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.update_item_note(
        db,
        user_id=user_id,
        session_id=session_id,
        item_id=item_id,
        note=data.note,
    )


@router.delete("/items/{item_id}", response_model=CartResponse)
async def remove_cart_item(
    item_id: uuid.UUID,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Remove a specific item from the cart."""
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.remove_item(
        db, user_id=user_id, session_id=session_id, item_id=item_id
    )


@router.delete("", response_model=CartResponse)
async def clear_cart(
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Remove all items from the cart."""
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.clear(db, user_id=user_id, session_id=session_id)


@router.put("/promo", response_model=CartResponse)
async def set_cart_promo(
    data: CartPromoRequest,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    Remember the code applied in the basket, or clear it with `null`.

    Records the code and nothing else. The discount is priced by
    `/promo-codes/validate` and charged by `create_order`, both of which
    validate afresh — so this endpoint cannot put a discount on an order and a
    code stored here that has since expired simply fails at the checkout like
    any other.
    """
    user_id, session_id = _resolve_identity(current_user, x_session_id)
    return await cart_service.set_promo_code(
        db, user_id=user_id, session_id=session_id, code=data.code
    )


@router.post("/merge", response_model=CartResponse)
async def merge_cart(
    data: CartMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Merge a guest session cart into the authenticated user's cart (call after login)."""
    if not current_user:
        raise UnauthorizedError("Authentication required to merge cart")
    return await cart_service.merge(
        db, guest_session_id=data.session_id, user_id=current_user.id
    )
