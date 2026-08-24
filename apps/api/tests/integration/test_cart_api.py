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
        # A `MagicMock` invents every attribute it is asked for, so a field
        # added to `CartResponse` arrives as a mock and fails response
        # validation. Stated rather than left to the mock's imagination.
        mock_cart.promo_code = None

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
        # A `MagicMock` invents every attribute it is asked for, so a field
        # added to `CartResponse` arrives as a mock and fails response
        # validation. Stated rather than left to the mock's imagination.
        mock_cart.promo_code = None

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
        # A `MagicMock` invents every attribute it is asked for, so a field
        # added to `CartResponse` arrives as a mock and fails response
        # validation. Stated rather than left to the mock's imagination.
        mock_cart.promo_code = None

        with patch(
            "app.api.v1.cart.cart_service.get_or_create",
            new=AsyncMock(return_value=mock_cart),
        ):
            response = await client.get(
                "/api/v1/cart",
                headers={"X-Session-Id": "my-session"},
            )

        assert response.status_code == 200

    # ─── A basket has to name whose it is ─────────────────────────────────
    #
    # These four go through the real `cart_service` rather than patching it,
    # which is the whole point: the bug was not in a router, it was that
    # `add_item` was the one path that reached `get_or_create_cart` without
    # first asking who was shopping. Patching the service would assert nothing
    # about the thing that broke.

    async def test_add_to_cart_without_identity_returns_400(self, client):
        """
        The regression. This answered **201** and quietly filed the item in a
        cart with no owner — the one every session-less shopper matched, and
        four customers' items accumulated in it. See migration 117.
        """
        response = await client.post(
            "/api/v1/cart/items",
            json={"product_id": _VALID_UUID, "quantity": 1},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Either user_id or session_id is required"

    async def test_update_item_without_identity_returns_400(self, client):
        """
        The symptom the customer saw: an add that worked, then a quantity
        stepper that did not. It is asserted alongside the add so that the two
        can never disagree again — whatever they answer, they answer together.
        """
        response = await client.put(
            f"/api/v1/cart/items/{_VALID_UUID}",
            json={"quantity": 2},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Either user_id or session_id is required"

    async def test_remove_item_without_identity_returns_400(self, client):
        response = await client.delete(f"/api/v1/cart/items/{_VALID_UUID}")

        assert response.status_code == 400
        assert response.json()["detail"] == "Either user_id or session_id is required"

    async def test_get_cart_without_identity_returns_400(self, client):
        """
        Unpatched, unlike `test_get_cart_without_session_creates_cart` above,
        which stubs `get_or_create` and so never reaches the guard.
        """
        response = await client.get("/api/v1/cart")

        assert response.status_code == 400
        assert response.json()["detail"] == "Either user_id or session_id is required"

    async def test_merge_cart_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/cart/merge",
            json={"session_id": "guest-session"},
        )
        assert response.status_code == 401
