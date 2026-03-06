from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
)
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_password_reset_token,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.models import User
from app.models.refresh_token import RefreshToken
from app.services import email_service
from app.schemas.user import (
    GuestSessionRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_token_response(user: User, db: AsyncSession) -> TokenResponse:
    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        is_admin=user.is_admin,
        is_guest=user.is_guest,
    )
    raw_refresh, refresh_hash = create_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    rt = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=refresh_hash,
        expires_at=expires_at,
        is_revoked=False,
    )
    db.add(rt)
    await db.flush()
    return TokenResponse(
        access_token=token,
        refresh_token=raw_refresh,
        user=UserResponse.model_validate(user),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
@limiter.limit("5/minute")
async def register(
    request: Request,
    body: UserCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    if result.scalar_one_or_none():
        raise ConflictError("An account with this email already exists")

    user = User(
        id=uuid.uuid4(),
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        is_active=True,
        is_admin=False,
        is_guest=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    background_tasks.add_task(email_service.send_welcome, user.email, user.first_name)
    return await _make_token_response(user, db)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    if (
        not user
        or not user.hashed_password
        or not verify_password(body.password, user.hashed_password)
    ):
        raise UnauthorizedError("Invalid email or password")
    if not user.is_active:
        raise ForbiddenError("Account is inactive")
    # If user was a guest before, upgrade them on login
    if user.is_guest:
        user.is_guest = False

    return await _make_token_response(user, db)


@router.post(
    "/guest",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest session",
)
async def create_guest_session(
    body: GuestSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = body.email or f"guest-{uuid.uuid4().hex[:8]}@guest.local"

    guest = User(
        id=uuid.uuid4(),
        email=email.lower(),
        hashed_password=None,
        first_name="Guest",
        last_name="User",
        is_active=True,
        is_admin=False,
        is_guest=True,
    )
    db.add(guest)
    await db.flush()
    await db.refresh(guest)

    return await _make_token_response(guest, db)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update current user profile",
)
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    if body.first_name is not None:
        current_user.first_name = body.first_name
    if body.last_name is not None:
        current_user.last_name = body.last_name
    if body.phone is not None:
        current_user.phone = body.phone

    await db.flush()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    summary="Request a password reset email",
)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    body: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    # Always return 200 to avoid email enumeration
    if user and user.hashed_password and not user.is_guest:
        reset_token = create_password_reset_token(str(user.id), user.email)
        background_tasks.add_task(
            email_service.send_password_reset, user.email, user.first_name, reset_token
        )

    return {"message": "If this email exists, a password reset link has been sent"}


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    summary="Reset password using a reset token",
)
async def reset_password(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        payload = decode_password_reset_token(body.token)
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise BadRequestError("Invalid or expired reset token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise BadRequestError("Invalid reset token")

    user.hashed_password = hash_password(body.new_password)
    await db.flush()

    return {"message": "Password updated successfully"}


# ---------------------------------------------------------------------------
# Refresh token rotation
# ---------------------------------------------------------------------------


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue a new access token",
)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()

    if not rt or rt.is_revoked or rt.expires_at < datetime.now(timezone.utc):
        raise UnauthorizedError("Invalid or expired refresh token")

    # Revoke old token (rotation)
    rt.is_revoked = True

    user_result = await db.execute(select(User).where(User.id == rt.user_id))
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    return await _make_token_response(user, db)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class LogoutRequest(BaseModel):
    refresh_token: str


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke refresh token (logout)",
)
async def logout(
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()
    if rt and not rt.is_revoked:
        rt.is_revoked = True
        await db.flush()


# ---------------------------------------------------------------------------
# Guest cleanup (admin only)
# ---------------------------------------------------------------------------


@router.delete(
    "/guests/cleanup",
    status_code=status.HTTP_200_OK,
    summary="Delete old guest users with no cart (admin only)",
)
async def cleanup_guests(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
) -> dict:
    from app.models.cart import Cart

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    cart_user_subq = (
        select(Cart.user_id).where(Cart.user_id.isnot(None)).scalar_subquery()
    )

    stmt = (
        delete(User)
        .where(
            User.is_guest == True,  # noqa: E712
            User.created_at < cutoff,
            User.id.not_in(cart_user_subq),
        )
        .returning(User.id)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    deleted_ids = result.scalars().all()
    await db.flush()
    return {"deleted": len(deleted_ids)}
