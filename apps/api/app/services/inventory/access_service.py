"""Branch authorization boundaries shared by inventory API surfaces."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.models.role import UserBranch
from app.models.user import User

__all__ = ["assert_branch_access", "branch_ids_for"]


async def assert_branch_access(
    db: AsyncSession, user: User, branch_id: uuid.UUID
) -> None:
    if user.is_admin or (user.role and user.role.is_super_admin):
        return
    allowed = await db.scalar(
        select(UserBranch.id).where(
            UserBranch.user_id == user.id,
            UserBranch.branch_id == branch_id,
        )
    )
    if allowed is None:
        raise ForbiddenError("You are not assigned to this branch")


def branch_ids_for(user: User):
    return select(UserBranch.branch_id).where(UserBranch.user_id == user.id)
