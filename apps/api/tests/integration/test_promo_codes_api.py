from __future__ import annotations

from unittest.mock import MagicMock


class TestPromoCodesEndpoints:
    async def test_validate_missing_body_returns_422(self, client):
        response = await client.post("/api/v1/promo-codes/validate", json={})
        assert response.status_code == 422

    async def test_validate_unknown_code_returns_valid_false(self, client):
        """With mock DB returning None, unknown promo code gives valid=False."""
        response = await client.post(
            "/api/v1/promo-codes/validate",
            json={"code": "DOESNOTEXIST", "order_subtotal": 100},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["discount_amount"] == 0.0

    async def test_validate_negative_subtotal_returns_422(self, client):
        response = await client.post(
            "/api/v1/promo-codes/validate",
            json={"code": "SAVE10", "order_subtotal": -1},
        )
        assert response.status_code == 422

    async def test_list_promo_codes_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/promo-codes")
        assert response.status_code == 401

    async def test_create_promo_code_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/promo-codes",
            json={
                "code": "SAVE10",
                "discount_type": "percentage",
                "discount_value": 10,
            },
        )
        assert response.status_code == 401

    async def test_create_promo_code_with_invalid_payload_returns_422(self, client):
        from app.core.deps import get_admin_user
        from app.main import app

        mock_admin = MagicMock()
        mock_admin.is_admin = True

        async def override_admin():
            return mock_admin

        app.dependency_overrides[get_admin_user] = override_admin
        try:
            response = await client.post("/api/v1/promo-codes", json={})
        finally:
            del app.dependency_overrides[get_admin_user]

        assert response.status_code == 422

    async def test_delete_promo_code_without_auth_returns_401(self, client):
        response = await client.delete("/api/v1/promo-codes/SAVE10")
        assert response.status_code == 401
