from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from .config import settings

ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Password hashing  (using bcrypt directly — passlib is unmaintained)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: str,
    email: str,
    is_admin: bool = False,
    is_guest: bool = False,
    expires_delta: timedelta | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin,
        "is_guest": is_guest,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_password_reset_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "email": email,
        "type": "reset",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and return token payload. Raises JWTError on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


def decode_password_reset_token(token: str) -> dict:
    """Decode a password reset token, validating its type."""
    payload = decode_token(token)
    if payload.get("type") != "reset":
        raise JWTError("Invalid token type")
    return payload


def create_refresh_token() -> tuple[str, str]:
    """Generate a refresh token. Returns (raw_token, token_hash).
    Store the hash in the DB; return the raw token to the client."""
    raw = secrets.token_urlsafe(64)
    h = hashlib.sha256(raw.encode()).hexdigest()
    return raw, h
