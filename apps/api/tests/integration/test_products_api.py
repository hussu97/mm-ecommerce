from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


class TestProductsEndpoints:
    async def test_list_products_returns_pagination_shape(self, client):
        with patch(
            "app.api.v1.products.product_service.get_all",
            new=AsyncMock(return_value=([], 0)),
        ):
            response = await client.get("/api/v1/products")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "pages" in data
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_products_pagination_defaults(self, client):
        with patch(
            "app.api.v1.products.product_service.get_all",
            new=AsyncMock(return_value=([], 0)),
        ):
            response = await client.get("/api/v1/products")

        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 20
        assert data["pages"] == 1

    async def test_featured_products_returns_list(self, client):
        with (
            patch(
                "app.api.v1.products.cache_get",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v1.products.product_service.get_featured",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.api.v1.products.cache_set",
                new=AsyncMock(),
            ),
        ):
            response = await client.get("/api/v1/products/featured")

        assert response.status_code == 200
        assert response.json() == []

    async def test_get_product_by_slug_not_found_returns_404(self, client):
        from fastapi import HTTPException

        with patch(
            "app.api.v1.products.product_service.get_by_slug",
            new=AsyncMock(side_effect=HTTPException(404, "Not found")),
        ):
            response = await client.get("/api/v1/products/non-existent-slug")

        assert response.status_code == 404

    async def test_create_product_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/products",
            json={"name": "Test", "slug": "test"},
        )
        assert response.status_code == 401

    async def test_create_product_with_invalid_payload_returns_422(self, client):
        from app.main import app
        from app.core.deps import get_admin_user

        mock_admin = MagicMock()
        mock_admin.is_admin = True

        async def override_admin():
            return mock_admin

        app.dependency_overrides[get_admin_user] = override_admin
        try:
            # Missing required fields (name, slug) → 422
            response = await client.post("/api/v1/products", json={})
        finally:
            del app.dependency_overrides[get_admin_user]

        assert response.status_code == 422

    async def test_list_products_invalid_page_param_returns_422(self, client):
        response = await client.get("/api/v1/products?page=0")
        assert response.status_code == 422
