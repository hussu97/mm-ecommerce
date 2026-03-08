from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


class TestCategoriesEndpoints:
    async def test_list_categories_returns_list(self, client):
        with (
            patch(
                "app.api.v1.categories.cache_get",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v1.categories.category_service.get_all",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.api.v1.categories.cache_set",
                new=AsyncMock(),
            ),
        ):
            response = await client.get("/api/v1/categories")

        assert response.status_code == 200
        assert response.json() == []

    async def test_list_categories_returns_cached_when_available(self, client):
        cached_data = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "Cakes",
                "slug": "cakes",
                "reference": None,
                "description": None,
                "image_url": None,
                "display_order": 0,
                "is_active": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "product_count": 3,
                "translations": {},
            }
        ]
        with patch(
            "app.api.v1.categories.cache_get",
            new=AsyncMock(return_value=cached_data),
        ):
            response = await client.get("/api/v1/categories")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Cakes"
        assert data[0]["product_count"] == 3

    async def test_get_category_by_slug_not_found_returns_404(self, client):
        from fastapi import HTTPException

        with patch(
            "app.api.v1.categories.category_service.get_by_slug",
            new=AsyncMock(side_effect=HTTPException(404, "Not found")),
        ):
            response = await client.get("/api/v1/categories/non-existent-slug")

        assert response.status_code == 404

    async def test_create_category_without_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/categories",
            json={"name": "Cakes", "slug": "cakes"},
        )
        assert response.status_code == 401

    async def test_create_category_with_invalid_payload_returns_422(self, client):
        from app.core.deps import get_admin_user
        from app.main import app

        mock_admin = MagicMock()
        mock_admin.is_admin = True

        async def override_admin():
            return mock_admin

        app.dependency_overrides[get_admin_user] = override_admin
        try:
            response = await client.post("/api/v1/categories", json={})
        finally:
            del app.dependency_overrides[get_admin_user]

        assert response.status_code == 422

    async def test_update_category_without_auth_returns_401(self, client):
        response = await client.put(
            "/api/v1/categories/cakes",
            json={"name": "Updated Cakes"},
        )
        assert response.status_code == 401

    async def test_delete_category_without_auth_returns_401(self, client):
        response = await client.delete("/api/v1/categories/cakes")
        assert response.status_code == 401
