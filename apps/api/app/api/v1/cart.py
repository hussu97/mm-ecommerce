from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_optional_user
from app.core.exceptions import UnauthorizedError
from app.models.user import User
from app.schemas.cart import CartItemCreate, CartItemUpdate, CartResponse
from app.services import cart_service

router = APIRouter()


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
