from __future__ import annotations


class TestDeliveryRates:
    async def test_get_rates_200(self, client):
        response = await client.get("/api/v1/delivery/rates")
        assert response.status_code == 200

    async def test_get_rates_has_free_threshold(self, client):
        data = (await client.get("/api/v1/delivery/rates")).json()
        assert "free_threshold" in data

    async def test_get_rates_publishes_no_price_list(self, client):
        """
        Areas and prices are not handed to the storefront. The fee comes from
        where the pin lands, and a table here would invite a guess from the
        address text instead.
        """
        data = (await client.get("/api/v1/delivery/rates")).json()
        assert "regions" not in data
        assert set(data) == {
            "free_threshold",
            "pickup_fee",
            "default_delivery_fee",
            # National scalars the checkout needs to explain the small-basket
            # fee. Not a price list: nothing here is keyed by a place.
            "low_order_fee",
            "low_order_threshold",
        }


class TestDeliveryCalculate:
    async def test_pickup_is_free(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={"delivery_method": "pickup", "subtotal": "50.00"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_free"] is True

    async def test_a_big_basket_alone_does_not_buy_free_delivery(self, client):
        """
        Free delivery is a property of the address as much as of the basket: it
        reaches the zones we price ourselves and no further. Without a pin there
        is no address, so there is nothing to promise yet.
        """
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "subtotal": "500.00",
            },
        )
        assert response.status_code == 200
        assert response.json()["is_free"] is False

    async def test_below_threshold_has_fee(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "subtotal": "100.00",
            },
        )
        assert response.status_code == 200
        assert float(response.json()["delivery_fee"]) > 0

    async def test_invalid_method_422(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={"delivery_method": "invalid", "subtotal": "100.00"},
        )
        assert response.status_code == 422
