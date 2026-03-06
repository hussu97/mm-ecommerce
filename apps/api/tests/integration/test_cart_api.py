from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


_VALID_UUID = "00000000-0000-0000-0000-000000000001"


class TestCartEndpoints:
    async def test_add_to_cart_qty_zero_returns_422(self, client):
        response = await client.post(
            "/api/v1/cart/items",
            json={"product_id": _VALID_UUID, "quantity": 0},
        )
        assert response.status_code == 422

    async def test_add_to_cart_qty_above_max_returns_422(self, client):
        response = await client.post(
            "/api/v1/cart/items",
            json={"product_id": _VALID_UUID, "quantity": 100},
        )
        assert response.status_code == 422

    async def test_add_to_cart_missing_product_id_returns_422(self, client):
        response = await client.post(
            "/api/v1/cart/items",
            json={"quantity": 1},
        )
        assert response.status_code == 422

    async def test_clear_cart_returns_cart_response(self, client):
        mock_cart = MagicMock()
        mock_cart.id = _VALID_UUID
        mock_cart.user_id = None
        mock_cart.session_id = "session-abc"
        mock_cart.items = []
        mock_cart.item_count = 0
        mock_cart.subtotal = 0.0

        with patch(
            "app.api.v1.cart.cart_service.clear",
            new=AsyncMock(return_value=mock_cart),
        ):
            response = await client.delete(
                "/api/v1/cart",
                headers={"X-Session-Id": "session-abc"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["items"] == []

    async def test_get_cart_without_session_creates_cart(self, client):
        mock_cart = MagicMock()
        mock_cart.id = _VALID_UUID
        mock_cart.user_id = None
        mock_cart.session_id = None
        mock_cart.items = []
        mock_cart.item_count = 0
        mock_cart.subtotal = 0.0

        with patch(
            "app.api.v1.cart.cart_service.get_or_create",
            new=AsyncMock(return_value=mock_cart),
        ):
            response = await client.get("/api/v1/cart")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data

    async def test_get_cart_with_session_header(self, client):
        mock_cart = MagicMock()
        mock_cart.id = _VALID_UUID
        mock_cart.user_id = None
        mock_cart.session_id = "my-session"
        mock_cart.items = []
        mock_cart.item_count = 0
        mock_cart.subtotal = 0.0

        with patch(
            "app.api.v1.cart.cart_service.get_or_create",
            new=AsyncMock(return_value=mock_cart),
        ):
            response = await client.get(
                "/api/v1/cart",
                headers={"X-Session-Id": "my-session"},
            )

        assert response.status_code == 200

    async def test_merge_cart_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/cart/merge",
            json={"session_id": "guest-session"},
        )
        assert response.status_code == 401
