from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from jose import JWTError
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

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
from app.models import AdminPasskey, User, WebAuthnChallenge
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
# Cookie helpers
# ---------------------------------------------------------------------------

_ACCESS_COOKIE = "mm_access_token"
_REFRESH_COOKIE = "mm_refresh_token"

SUPERADMIN_EMAIL = "admin@meltingmomentscakes.com"
ADMIN_PASSKEY_EXCLUDED_EMAILS = {SUPERADMIN_EMAIL}
WEBAUTHN_CHALLENGE_TTL_MINUTES = 10


def _set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    response.set_cookie(
        key=_ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/api/v1/auth",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=_ACCESS_COOKIE, path="/")
    response.delete_cookie(key=_REFRESH_COOKIE, path="/api/v1/auth")


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


def _admin_origin() -> str:
    return settings.ADMIN_URL.rstrip("/")


def _admin_rp_id() -> str:
    hostname = urlparse(settings.ADMIN_URL).hostname
    return hostname or "localhost"


def _is_passkey_allowed(user: User) -> bool:
    return (
        bool(user.is_admin)
        and bool(user.is_active)
        and user.email.lower() not in ADMIN_PASSKEY_EXCLUDED_EMAILS
    )


def _options_payload(options: object) -> dict:
    return json.loads(options_to_json(options))


async def _delete_expired_webauthn_challenges(db: AsyncSession) -> None:
    await db.execute(
        delete(WebAuthnChallenge).where(
            WebAuthnChallenge.expires_at < datetime.now(timezone.utc)
        )
    )


async def _store_webauthn_challenge(
    *,
    db: AsyncSession,
    user: User | None,
    email: str,
    challenge: bytes,
    ceremony: str,
) -> None:
    await _delete_expired_webauthn_challenges(db)
    await db.execute(
        delete(WebAuthnChallenge).where(
            WebAuthnChallenge.email == email.lower(),
            WebAuthnChallenge.ceremony == ceremony,
        )
    )
    db.add(
        WebAuthnChallenge(
            id=uuid.uuid4(),
            user_id=user.id if user else None,
            email=email.lower(),
            challenge=bytes_to_base64url(challenge),
            ceremony=ceremony,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=WEBAUTHN_CHALLENGE_TTL_MINUTES),
        )
    )
    await db.flush()


async def _get_webauthn_challenge(
    *,
    db: AsyncSession,
    email: str,
    ceremony: str,
    user_id: uuid.UUID | None = None,
) -> WebAuthnChallenge:
    stmt = (
        select(WebAuthnChallenge)
        .where(
            WebAuthnChallenge.email == email.lower(),
            WebAuthnChallenge.ceremony == ceremony,
            WebAuthnChallenge.expires_at >= datetime.now(timezone.utc),
        )
        .order_by(WebAuthnChallenge.created_at.desc())
        .limit(1)
    )
    if user_id is not None:
        stmt = stmt.where(WebAuthnChallenge.user_id == user_id)
    result = await db.execute(stmt)
    challenge = result.scalar_one_or_none()
    if not challenge:
        raise BadRequestError("Passkey challenge has expired. Please try again.")
    return challenge


class AdminLoginOptionsRequest(BaseModel):
    email: str


class AdminLoginOptionsResponse(BaseModel):
    email: str
    is_admin: bool
    has_passkey: bool
    password_enabled: bool
    passkey_allowed: bool
    is_superadmin: bool


class PasskeyRegistrationOptionsResponse(BaseModel):
    options: dict


class PasskeyRegistrationVerifyRequest(BaseModel):
    credential: dict
    name: str | None = Field(default=None, max_length=100)


class AdminPasskeyResponse(BaseModel):
    id: str
    name: str | None
    created_at: str
    last_used_at: str | None


class PasskeyLoginOptionsRequest(BaseModel):
    email: str


class PasskeyLoginOptionsResponse(BaseModel):
    options: dict


class PasskeyLoginVerifyRequest(BaseModel):
    email: str
    credential: dict


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
    response: Response,
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
        phone=body.phone,
        is_active=True,
        is_admin=False,
        is_guest=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    background_tasks.add_task(email_service.send_welcome, user.email)
    token_resp = await _make_token_response(user, db)
    _set_auth_cookies(response, token_resp.access_token, token_resp.refresh_token)
    return token_resp


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
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

    token_resp = await _make_token_response(user, db)
    _set_auth_cookies(response, token_resp.access_token, token_resp.refresh_token)
    return token_resp


@router.post(
    "/admin/login-options",
    response_model=AdminLoginOptionsResponse,
    summary="Resolve admin login options for an email",
)
@limiter.limit("10/minute")
async def admin_login_options(
    request: Request,
    body: AdminLoginOptionsRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminLoginOptionsResponse:
    email = body.email.lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or not user.is_admin:
        return AdminLoginOptionsResponse(
            email=email,
            is_admin=False,
            has_passkey=False,
            password_enabled=False,
            passkey_allowed=False,
            is_superadmin=False,
        )

    is_superadmin = email == SUPERADMIN_EMAIL
    passkey_allowed = _is_passkey_allowed(user)
    passkey_count = 0
    if passkey_allowed:
        passkey_count = (
            await db.execute(
                select(func.count(AdminPasskey.id)).where(
                    AdminPasskey.user_id == user.id
                )
            )
        ).scalar_one()

    return AdminLoginOptionsResponse(
        email=email,
        is_admin=True,
        has_passkey=passkey_count > 0,
        password_enabled=bool(user.hashed_password),
        passkey_allowed=passkey_allowed,
        is_superadmin=is_superadmin,
    )


@router.post(
    "/admin/passkeys/register/options",
    response_model=PasskeyRegistrationOptionsResponse,
    summary="Create passkey registration options for the current admin",
)
async def passkey_registration_options(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> PasskeyRegistrationOptionsResponse:
    if not _is_passkey_allowed(current_user):
        raise ForbiddenError("Passkeys are not available for this admin account")

    existing = (
        (
            await db.execute(
                select(AdminPasskey).where(AdminPasskey.user_id == current_user.id)
            )
        )
        .scalars()
        .all()
    )
    options = generate_registration_options(
        rp_id=_admin_rp_id(),
        rp_name="Melting Moments Admin",
        user_id=current_user.id.bytes,
        user_name=current_user.email,
        user_display_name=current_user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
            for passkey in existing
        ],
    )
    await _store_webauthn_challenge(
        db=db,
        user=current_user,
        email=current_user.email,
        challenge=options.challenge,
        ceremony="registration",
    )
    return PasskeyRegistrationOptionsResponse(options=_options_payload(options))


@router.post(
    "/admin/passkeys/register/verify",
    response_model=AdminPasskeyResponse,
    summary="Verify and store a new passkey for the current admin",
)
async def passkey_registration_verify(
    body: PasskeyRegistrationVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> AdminPasskeyResponse:
    if not _is_passkey_allowed(current_user):
        raise ForbiddenError("Passkeys are not available for this admin account")

    challenge = await _get_webauthn_challenge(
        db=db,
        email=current_user.email,
        ceremony="registration",
        user_id=current_user.id,
    )
    try:
        verified = verify_registration_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=_admin_rp_id(),
            expected_origin=_admin_origin(),
            require_user_verification=False,
        )
    except Exception as exc:
        raise BadRequestError("Passkey registration failed. Please try again.") from exc

    credential_id = bytes_to_base64url(verified.credential_id)
    existing = await db.execute(
        select(AdminPasskey).where(AdminPasskey.credential_id == credential_id)
    )
    if existing.scalar_one_or_none():
        raise ConflictError("This passkey is already registered")

    response_payload = body.credential.get("response", {})
    transports = response_payload.get("transports")
    passkey = AdminPasskey(
        id=uuid.uuid4(),
        user_id=current_user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        name=body.name or "Admin passkey",
        transports=transports if isinstance(transports, list) else None,
    )
    db.add(passkey)
    await db.delete(challenge)
    await db.flush()
    await db.refresh(passkey)

    return AdminPasskeyResponse(
        id=str(passkey.id),
        name=passkey.name,
        created_at=passkey.created_at.isoformat(),
        last_used_at=None,
    )


@router.get(
    "/admin/passkeys",
    response_model=list[AdminPasskeyResponse],
    summary="List passkeys for the current admin",
)
async def list_admin_passkeys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> list[AdminPasskeyResponse]:
    if not _is_passkey_allowed(current_user):
        return []

    passkeys = (
        (
            await db.execute(
                select(AdminPasskey)
                .where(AdminPasskey.user_id == current_user.id)
                .order_by(AdminPasskey.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        AdminPasskeyResponse(
            id=str(passkey.id),
            name=passkey.name,
            created_at=passkey.created_at.isoformat(),
            last_used_at=passkey.last_used_at.isoformat()
            if passkey.last_used_at
            else None,
        )
        for passkey in passkeys
    ]


@router.delete(
    "/admin/passkeys/{passkey_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one passkey for the current admin",
)
async def delete_admin_passkey(
    passkey_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> None:
    result = await db.execute(
        select(AdminPasskey).where(
            AdminPasskey.id == passkey_id,
            AdminPasskey.user_id == current_user.id,
        )
    )
    passkey = result.scalar_one_or_none()
    if passkey:
        await db.delete(passkey)
        await db.flush()


@router.post(
    "/admin/passkeys/login/options",
    response_model=PasskeyLoginOptionsResponse,
    summary="Create passkey login options for an admin email",
)
@limiter.limit("10/minute")
async def passkey_login_options(
    request: Request,
    body: PasskeyLoginOptionsRequest,
    db: AsyncSession = Depends(get_db),
) -> PasskeyLoginOptionsResponse:
    email = body.email.lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not _is_passkey_allowed(user):
        raise UnauthorizedError("Passkey login is not available for this account")

    passkeys = (
        (await db.execute(select(AdminPasskey).where(AdminPasskey.user_id == user.id)))
        .scalars()
        .all()
    )
    if not passkeys:
        raise BadRequestError("No passkeys are registered for this account")

    options = generate_authentication_options(
        rp_id=_admin_rp_id(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
            for passkey in passkeys
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    await _store_webauthn_challenge(
        db=db,
        user=user,
        email=email,
        challenge=options.challenge,
        ceremony="authentication",
    )
    return PasskeyLoginOptionsResponse(options=_options_payload(options))


@router.post(
    "/admin/passkeys/login/verify",
    response_model=TokenResponse,
    summary="Verify a passkey login response and create an admin session",
)
@limiter.limit("10/minute")
async def passkey_login_verify(
    request: Request,
    response: Response,
    body: PasskeyLoginVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = body.email.lower()
    credential_id = body.credential.get("id")
    if not isinstance(credential_id, str):
        raise BadRequestError("Invalid passkey response")

    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    if not user or not _is_passkey_allowed(user):
        raise UnauthorizedError("Passkey login is not available for this account")

    passkey_result = await db.execute(
        select(AdminPasskey).where(
            AdminPasskey.user_id == user.id,
            AdminPasskey.credential_id == credential_id,
        )
    )
    passkey = passkey_result.scalar_one_or_none()
    if not passkey:
        raise UnauthorizedError("Passkey not recognized")

    challenge = await _get_webauthn_challenge(
        db=db,
        email=email,
        ceremony="authentication",
        user_id=user.id,
    )
    try:
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=_admin_rp_id(),
            expected_origin=_admin_origin(),
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=False,
        )
    except Exception as exc:
        raise UnauthorizedError("Passkey login failed") from exc

    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = datetime.now(timezone.utc)
    await db.delete(challenge)
    token_resp = await _make_token_response(user, db)
    _set_auth_cookies(response, token_resp.access_token, token_resp.refresh_token)
    return token_resp


@router.post(
    "/guest",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest session",
)
async def create_guest_session(
    response: Response,
    body: GuestSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = body.email or f"guest-{uuid.uuid4().hex[:8]}@guest.local"

    guest = User(
        id=uuid.uuid4(),
        email=email.lower(),
        hashed_password=None,
        is_active=True,
        is_admin=False,
        is_guest=True,
    )
    db.add(guest)
    await db.flush()
    await db.refresh(guest)

    token_resp = await _make_token_response(guest, db)
    _set_auth_cookies(response, token_resp.access_token, token_resp.refresh_token)
    return token_resp


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
    if user and not user.is_guest:
        reset_token = create_password_reset_token(str(user.id), user.email)
        background_tasks.add_task(
            email_service.send_password_reset, user.email, reset_token
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
    refresh_token: str | None = None


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue a new access token",
)
async def refresh_token(
    request: Request,
    response: Response,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    raw = body.refresh_token or request.cookies.get(_REFRESH_COOKIE)
    if not raw:
        raise UnauthorizedError("Invalid or expired refresh token")

    token_hash = hashlib.sha256(raw.encode()).hexdigest()

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

    token_resp = await _make_token_response(user, db)
    _set_auth_cookies(response, token_resp.access_token, token_resp.refresh_token)
    return token_resp


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke refresh token (logout)",
)
async def logout(
    request: Request,
    response: Response,
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    raw = body.refresh_token or request.cookies.get(_REFRESH_COOKIE)
    if raw:
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        rt = result.scalar_one_or_none()
        if rt and not rt.is_revoked:
            rt.is_revoked = True
            await db.flush()
    _clear_auth_cookies(response)


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
