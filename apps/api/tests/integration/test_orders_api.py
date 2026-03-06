from __future__ import annotations


class TestOrdersEndpoints:
    async def test_create_order_missing_body_returns_422(self, client):
        # No auth required (get_optional_user), but body is required
        response = await client.post("/api/v1/orders", json={})
        assert response.status_code == 422

    async def test_list_my_orders_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/orders")
        assert response.status_code == 401

    async def test_list_admin_orders_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/orders/admin/all")
        assert response.status_code == 401

    async def test_update_order_status_without_auth_returns_401(self, client):
        response = await client.put(
            "/api/v1/orders/MM-2026-0001/status",
            json={"status": "confirmed"},
        )
        assert response.status_code == 401

    async def test_track_order_missing_fields_returns_422(self, client):
        response = await client.post("/api/v1/orders/track", json={})
        assert response.status_code == 422

    async def test_track_order_not_found_returns_404(self, client, mock_db):
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/orders/track",
            json={"order_number": "MM-2026-9999", "email": "nobody@example.com"},
        )
        assert response.status_code == 404
