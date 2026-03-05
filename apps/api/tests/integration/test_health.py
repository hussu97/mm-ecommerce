from __future__ import annotations


class TestHealth:
    async def test_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_status_ok(self, client):
        data = (await client.get("/health")).json()
        assert data["status"] == "ok"

    async def test_service_name(self, client):
        data = (await client.get("/health")).json()
        assert data["service"] == "mm-api"
