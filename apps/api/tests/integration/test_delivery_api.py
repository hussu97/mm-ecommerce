from __future__ import annotations


class TestDeliveryRates:
    async def test_get_rates_200(self, client):
        response = await client.get("/api/v1/delivery/rates")
        assert response.status_code == 200

    async def test_get_rates_has_standard_rate(self, client):
        data = (await client.get("/api/v1/delivery/rates")).json()
        assert "standard_rate" in data

    async def test_get_rates_has_free_threshold(self, client):
        data = (await client.get("/api/v1/delivery/rates")).json()
        assert "free_threshold" in data


class TestDeliveryCalculate:
    async def test_pickup_is_free(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={"delivery_method": "pickup", "subtotal": "50.00"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_free"] is True

    async def test_dubai_below_threshold(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "region": "dubai",
                "subtotal": "100.00",
            },
        )
        assert response.status_code == 200
        assert float(response.json()["delivery_fee"]) == 35.0

    async def test_free_above_threshold(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "region": "dubai",
                "subtotal": "200.00",
            },
        )
        assert response.status_code == 200
        assert response.json()["is_free"] is True

    async def test_remote_zone_rate(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "region": "abu_dhabi",
                "subtotal": "100.00",
            },
        )
        assert response.status_code == 200
        assert float(response.json()["delivery_fee"]) == 50.0

    async def test_invalid_method_422(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={"delivery_method": "invalid", "subtotal": "100.00"},
        )
        assert response.status_code == 422
