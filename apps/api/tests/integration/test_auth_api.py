from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


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

    async def test_logout_missing_body_returns_422(self, client):
        """Logout requires refresh_token in body; missing body returns 422."""
        response = await client.post("/api/v1/auth/logout", json={})
        assert response.status_code == 422
