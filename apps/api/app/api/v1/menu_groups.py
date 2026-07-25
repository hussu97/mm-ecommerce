"""
The register's menu tree.

Groups nest, and a product reaches the terminal through the tree — so this is
what an operator uses to decide the register's menu, the same way Foodics does
it. The read endpoints are open to any signed-in staff member because the
terminal fetches the tree to lay its buttons out; writing needs an admin.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.models import User
from app.schemas.menu_group import (
    MenuGroupCreate,
    MenuGroupNode,
    MenuGroupResponse,
    MenuGroupUpdate,
)
from app.services import menu_group_service

router = APIRouter()


def _to_response(group) -> MenuGroupResponse:
    return MenuGroupResponse(
        id=group.id,
        name=group.name,
        name_localized=group.name_localized,
        translations=group.translations or {},
        reference=group.reference,
        image_url=group.image_url,
        parent_id=group.parent_id,
        display_order=group.display_order,
        is_active=group.is_active,
        product_ids=[m.product_id for m in group.members],
    )


@router.get("/tree", response_model=list[MenuGroupNode])
async def get_tree(
    include_inactive: bool = Query(
        False, description="Include groups switched off, for the console's builder"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """The whole menu, nested. This is what the terminal renders."""
    return await menu_group_service.list_tree(db, include_inactive=include_inactive)


@router.get("/{group_id}", response_model=MenuGroupResponse)
async def get_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return _to_response(await menu_group_service.get(db, group_id))


@router.post("", response_model=MenuGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: MenuGroupCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    group = await menu_group_service.create(db, data.model_dump())
    return _to_response(group)


@router.patch("/{group_id}", response_model=MenuGroupResponse)
async def update_group(
    group_id: uuid.UUID,
    data: MenuGroupUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    group = await menu_group_service.update(
        db, group_id, data.model_dump(exclude_unset=True)
    )
    return _to_response(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """Removes the group and everything nested under it."""
    await menu_group_service.delete(db, group_id)
