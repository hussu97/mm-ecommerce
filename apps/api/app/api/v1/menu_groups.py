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

from app.core.deps import get_current_active_user, get_db
from app.core.permissions import require
from app.models import User
from app.schemas.menu_group import (
    MenuGroupClone,
    MenuGroupCreate,
    MenuGroupNode,
    MenuGroupResponse,
    MenuGroupUpdate,
)
from app.services.catalog import menu_group_service

router = APIRouter()


def _to_response(group) -> MenuGroupResponse:
    return MenuGroupResponse(
        id=group.id,
        name=group.name,
        name_localized=group.name_localized,
        translations=group.translations or {},
        reference=group.reference,
        image_url=group.image_url,
        root_kind=group.root_kind,
        branch_id=group.branch_id,
        root_id=group.root_id,
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
    branch_id: uuid.UUID | None = Query(
        None, description="Only this shop's menu — what a terminal fetches"
    ),
    root_kind: str | None = Query(
        None, description="'branch' for the shop trees, 'integrator' for the sync menu"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """A menu, nested. A terminal fetches its own branch's tree; the console can
    ask for every tree, one branch's, or the integrator menu."""
    return await menu_group_service.list_tree(
        db,
        include_inactive=include_inactive,
        branch_id=branch_id,
        root_kind=root_kind,
    )


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
    _: User = Depends(require("catalogue.manage")),
):
    group = await menu_group_service.create(db, data.model_dump())
    return _to_response(group)


@router.post(
    "/{group_id}/clone",
    response_model=MenuGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_group(
    group_id: uuid.UUID,
    data: MenuGroupClone,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
):
    """Open a new shop's menu as a copy of an existing branch root."""
    group = await menu_group_service.clone_tree(
        db, group_id, into_branch_id=data.branch_id, name=data.name
    )
    return _to_response(group)


@router.patch("/{group_id}", response_model=MenuGroupResponse)
async def update_group(
    group_id: uuid.UUID,
    data: MenuGroupUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
):
    group = await menu_group_service.update(
        db, group_id, data.model_dump(exclude_unset=True)
    )
    return _to_response(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.manage")),
):
    """Removes the group and everything nested under it."""
    await menu_group_service.delete(db, group_id)
