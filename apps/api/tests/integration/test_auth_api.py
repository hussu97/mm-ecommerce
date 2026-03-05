from __future__ import annotations

from unittest.mock import MagicMock


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
