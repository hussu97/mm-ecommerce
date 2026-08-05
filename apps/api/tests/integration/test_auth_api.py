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
