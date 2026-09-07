from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services import turnstile_service


@pytest.fixture(autouse=True)
def turnstile_off(monkeypatch):
    """
    These tests are about duplicate emails and enumeration, not bot checks.

    Pinned rather than assumed: a developer machine with the real secret in its
    `.env` had every one of them failing with a 400, because `Settings` reads
    that file and the suite inherited whatever happened to be configured. A test
    whose result depends on a gitignored file is a test that passes in CI and
    fails on the one machine that matters.

    `TestTurnstileOnAuthEndpoints` below covers it switched on.
    """
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")


class TestAuthEndpoints:
    async def test_me_without_token_returns_401(self, client):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_login_unknown_email_returns_401(self, client):
        # mock_db.execute returns result where scalar_one_or_none() returns None (no user)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "unknown@example.com", "password": "password123"},
        )
        assert response.status_code == 401

    async def test_register_duplicate_email_returns_409(self, client, mock_db):
        # Simulate existing user found in DB
        existing_user = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "existing@example.com",
                "password": "password123",
                "first_name": "John",
                "last_name": "Doe",
            },
        )
        assert response.status_code == 409

    async def test_login_invalid_email_format_returns_422(self, client):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": "password123"},
        )
        assert response.status_code == 422

    async def test_create_guest_session_returns_201(self, client):
        from app.schemas.user import TokenResponse, UserResponse

        mock_user = UserResponse(
            id=uuid.uuid4(),
            email="guest@guest.local",
            first_name="Guest",
            last_name="User",
            phone=None,
            is_active=True,
            is_admin=False,
            is_guest=True,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        mock_token = TokenResponse(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            user=mock_user,
        )
        with patch(
            "app.api.v1.auth._make_token_response",
            new=AsyncMock(return_value=mock_token),
        ):
            response = await client.post("/api/v1/auth/guest", json={})

        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data

    async def test_forgot_password_always_returns_200(self, client):
        """Even for unknown emails, forgot-password returns 200 to prevent enumeration."""
        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "nobody@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    async def test_refresh_with_invalid_token_returns_401(self, client):
        """Invalid refresh token (not in DB) returns 401."""
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid-token-that-does-not-exist"},
        )
        assert response.status_code == 401

    async def test_logout_empty_body_returns_204(self, client):
        """Logout accepts empty body — refresh_token is optional (also read from cookie)."""
        response = await client.post("/api/v1/auth/logout", json={})
        assert response.status_code == 204


class TestTurnstileOnAuthEndpoints:
    """
    The two endpoints that make us send mail to an address the caller typed.

    Covered here because the wiring is the part that breaks: the service had
    tests from the day it was written, and it was the endpoints that quietly
    refused everything the moment a real secret appeared in a `.env`.
    """

    @pytest.fixture(autouse=True)
    def turnstile_on(self, monkeypatch):
        monkeypatch.setattr(
            settings,
            "TURNSTILE_SECRET_KEY",
            turnstile_service.TEST_SECRET_ALWAYS_PASSES,
        )

    async def test_register_without_a_token_is_refused(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "bot@example.com", "password": "password123"},
        )
        assert response.status_code == 400
        # One message, whatever tripped. A specific one tells a bot which guess
        # was closest and a customer nothing they can act on.
        body = str(response.json()).lower()
        assert "verify that you" in body
        for leak in ("turnstile", "token", "cloudflare", "secret"):
            assert leak not in body, f"the refusal mentions {leak!r}"

    async def test_register_with_a_solved_challenge_gets_through(self, client, mock_db):
        existing_user = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "existing@example.com",
                "password": "password123",
                "turnstile_token": "XXXX.DUMMY.TOKEN.XXXX",
            },
        )
        # Past the bot check and into the real logic — 409 for the duplicate,
        # which is the proof that the challenge is not what stopped it.
        assert response.status_code == 409

    async def test_forgot_password_without_a_token_is_refused(self, client):
        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "someone@example.com"},
        )
        assert response.status_code == 400

    async def test_forgot_password_with_a_solved_challenge_still_says_nothing(
        self, client, mock_db
    ):
        """
        And the enumeration guarantee survives the bot check: a solved challenge
        for an address that does not exist gets the same 200 and the same words
        as one that does.
        """
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/forgot-password",
            json={
                "email": "nobody@example.com",
                "turnstile_token": "XXXX.DUMMY.TOKEN.XXXX",
            },
        )
        assert response.status_code == 200
        assert "if this email exists" in response.json()["message"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# F-POS-24 — session revocation, against a real database.
#
# Refresh-token rotation gained reuse detection (a replayed spent token revokes
# its whole family); password reset now expires the reset JWT and revokes every
# session; and deactivating a staff account revokes its refresh tokens. These
# move real rows across connections, which the mocked `client` fixture above
# cannot express — they need Postgres.
# ─────────────────────────────────────────────────────────────────────────────

import os  # noqa: E402
from datetime import timedelta  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.deps import get_current_active_user, get_db  # noqa: E402
from app.core.security import (  # noqa: E402
    create_password_reset_token,
    create_refresh_token,
    hash_password,
)
from app.main import app as web_app  # noqa: E402
from app.models.refresh_token import RefreshToken  # noqa: E402
from app.models.user import User  # noqa: E402

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
_needs_db = pytest.mark.skipif(
    not _DB_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@pytest.fixture
async def _engine():
    engine = create_async_engine(_DB_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker(_engine):
    return async_sessionmaker(_engine, expire_on_commit=False)


@pytest.fixture
async def revocation_client(sessionmaker, monkeypatch):
    """An ASGI client whose `get_db` commits like production, over the test DB.

    Also points the auth module's side-session factory (used by the reuse-
    detection family revoke, which commits and then 401s) at the test database.
    """

    async def override_get_db():
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    web_app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.api.v1.auth.AsyncSessionFactory", sessionmaker)
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://testserver"
    ) as ac:
        yield ac
    web_app.dependency_overrides.pop(get_db, None)


async def _make_user(sessionmaker, **kw) -> uuid.UUID:
    async with sessionmaker() as s:
        user = User(
            email=f"fpos24-{uuid.uuid4().hex}@example.com",
            hashed_password=hash_password("OldPassw0rd!"),
            is_active=True,
            **kw,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _add_refresh_token(sessionmaker, user_id, family) -> str:
    raw, token_hash = create_refresh_token()
    async with sessionmaker() as s:
        s.add(
            RefreshToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                is_revoked=False,
                token_family=family,
            )
        )
        await s.commit()
    return raw


async def _cleanup(sessionmaker, user_id) -> None:
    async with sessionmaker() as s:
        await s.execute(
            RefreshToken.__table__.delete().where(RefreshToken.user_id == user_id)
        )
        await s.execute(User.__table__.delete().where(User.id == user_id))
        await s.commit()


@_needs_db
class TestSessionRevocation:
    async def test_replayed_refresh_token_revokes_the_whole_family(
        self, revocation_client, sessionmaker
    ):
        user_id = await _make_user(sessionmaker)
        family = uuid.uuid4()
        raw1 = await _add_refresh_token(sessionmaker, user_id, family)
        try:
            # A first rotation succeeds and hands back a new token in the family.
            ok = await revocation_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": raw1}
            )
            assert ok.status_code == 200
            raw2 = ok.json()["refresh_token"]

            # Replaying the now-spent raw1 is the fingerprint of a stolen token.
            replay = await revocation_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": raw1}
            )
            assert replay.status_code == 401

            # The reaction is family-wide: even the legitimate raw2 is now dead.
            after = await revocation_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": raw2}
            )
            assert after.status_code == 401

            async with sessionmaker() as s:
                rows = (
                    (
                        await s.execute(
                            select(RefreshToken).where(
                                RefreshToken.token_family == family
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert rows, "the family should still have rows, all revoked"
            assert all(r.is_revoked for r in rows)
        finally:
            await _cleanup(sessionmaker, user_id)

    async def test_password_reset_revokes_sessions_and_is_single_use(
        self, revocation_client, sessionmaker
    ):
        user_id = await _make_user(sessionmaker)
        family = uuid.uuid4()
        await _add_refresh_token(sessionmaker, user_id, family)
        async with sessionmaker() as s:
            email = (await s.get(User, user_id)).email
        reset = create_password_reset_token(str(user_id), email)
        try:
            first = await revocation_client.post(
                "/api/v1/auth/reset-password",
                json={"token": reset, "new_password": "BrandNewPassw0rd!"},
            )
            assert first.status_code == 200

            async with sessionmaker() as s:
                user = await s.get(User, user_id)
                assert user.password_changed_at is not None
                rows = (
                    (
                        await s.execute(
                            select(RefreshToken).where(RefreshToken.user_id == user_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            assert rows and all(r.is_revoked for r in rows)

            # The same reset token cannot drive a second change.
            replay = await revocation_client.post(
                "/api/v1/auth/reset-password",
                json={"token": reset, "new_password": "AnotherPassw0rd!"},
            )
            assert replay.status_code == 400
        finally:
            await _cleanup(sessionmaker, user_id)

    async def test_deactivating_staff_revokes_their_refresh_tokens(
        self, revocation_client, sessionmaker
    ):
        admin_id = await _make_user(sessionmaker, is_admin=True, is_staff=True)
        staff_id = await _make_user(sessionmaker, is_staff=True)
        family = uuid.uuid4()
        await _add_refresh_token(sessionmaker, staff_id, family)
        async with sessionmaker() as s:
            admin = await s.get(User, admin_id)

        web_app.dependency_overrides[get_current_active_user] = lambda: admin
        try:
            resp = await revocation_client.delete(f"/api/v1/staff/{staff_id}")
            assert resp.status_code == 204

            async with sessionmaker() as s:
                staff = await s.get(User, staff_id)
                assert staff.is_active is False
                assert staff.password_changed_at is not None
                rows = (
                    (
                        await s.execute(
                            select(RefreshToken).where(RefreshToken.user_id == staff_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            assert rows and all(r.is_revoked for r in rows)
        finally:
            web_app.dependency_overrides.pop(get_current_active_user, None)
            await _cleanup(sessionmaker, staff_id)
            await _cleanup(sessionmaker, admin_id)
