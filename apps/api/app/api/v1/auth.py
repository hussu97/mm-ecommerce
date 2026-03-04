from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import BadRequestError, ConflictError
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    decode_password_reset_token,
    hash_password,
    verify_password,
)
from app.models import User
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

def _make_token_response(user: User) -> TokenResponse:
    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        is_admin=user.is_admin,
        is_guest=user.is_guest,
    )
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def register(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

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

    # TODO (Prompt 7): send_welcome_email(user) in background
    return _make_token_response(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )
    # If user was a guest before, upgrade them on login
    if user.is_guest:
        user.is_guest = False

    return _make_token_response(user)


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

    return _make_token_response(guest)


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
async def forgot_password(
    body: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    # Always return 200 to avoid email enumeration
    if user and user.hashed_password and not user.is_guest:
        reset_token = create_password_reset_token(str(user.id), user.email)
        # TODO (Prompt 7): background_tasks.add_task(send_password_reset, user, reset_token)
        _ = reset_token  # suppress unused warning until email service is wired up

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")

    user.hashed_password = hash_password(body.new_password)
    await db.flush()

    return {"message": "Password updated successfully"}
