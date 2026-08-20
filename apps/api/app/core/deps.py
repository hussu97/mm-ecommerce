from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionFactory
from app.core.security import decode_token
from app.models import User
from app.models.branch import Branch

logger = logging.getLogger(__name__)

__all__ = [
    "get_admin_user",
    "get_current_active_user",
    "get_current_user",
    "get_db",
    "get_optional_user",
    "oauth2_scheme",
]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def _get_user_from_token(
    token: str | None,
    db: AsyncSession,
    required: bool = True,
) -> User | None:
    if not token:
        if required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise JWTError("Wrong token type")
        user_id_str: str | None = payload.get("sub")
        if not user_id_str:
            raise JWTError("Missing subject")
        user_id = uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user and required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    resolved = request.cookies.get("mm_access_token") or token
    return await _get_user_from_token(resolved, db, required=True)  # type: ignore[return-value]


async def get_current_active_user(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )
    # A customer's token and a cashier's token are the same format from the same
    # `create_access_token`, and this only ever checked `is_active` — so a
    # storefront customer's JWT satisfied every POS route's authentication and
    # was stopped only by `user.can(...)` returning False for a role-less
    # account. That is one missing permission check away from a customer editing
    # a live check, which is exactly what `add_item` was.
    #
    # Staff-only is true of the register API by definition: the terminal is the
    # only client, and every person holding one is on the payroll.
    if getattr(request.app.state, "is_pos_app", False) and not (
        current_user.is_staff or current_user.is_admin
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required",
        )
    return current_user


async def get_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user


async def get_optional_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Returns the current user if authenticated, otherwise None (for guest browsing)."""
    resolved = request.cookies.get("mm_access_token") or token
    return await _get_user_from_token(resolved, db, required=False)


async def browsing_branch(
    branch_id: uuid.UUID | None = Query(
        None,
        description=(
            "The kitchen the shopper's pin resolves to. Given one, the "
            "storefront answers for what that branch can make; omitted, for "
            "what any branch can. Read `branch_id` off GET /delivery/area."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID | None:
    """
    The branch this shopper is browsing as, or None to answer for the estate.

    Validated rather than trusted. The id arrives from a cookie the browser
    wrote and can be stale — a branch closed since the tab was opened, or a
    hand-edited value — and a catalogue filtered on a branch that no longer
    takes orders would quietly go empty. None is the widest honest answer:
    everything some branch can still make.

    A dependency rather than a helper because every storefront read needs the
    same three lines, and the one that grows its own copy is the one that ends
    up trusting the parameter.
    """
    if branch_id is None:
        return None
    branch = await db.get(Branch, branch_id)
    if branch is None or branch.deleted_at is not None or not branch.is_active:
        logger.info("Ignoring browsing branch %s, which cannot take orders", branch_id)
        return None
    return branch.id
