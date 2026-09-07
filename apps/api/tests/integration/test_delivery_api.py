from __future__ import annotations

import pytest


class TestDeliveryRates:
    async def test_get_rates_200(self, client):
        response = await client.get("/api/v1/delivery/rates")
        assert response.status_code == 200

    async def test_get_rates_has_free_threshold(self, client):
        data = (await client.get("/api/v1/delivery/rates")).json()
        assert "pickup_fee" in data

    async def test_get_rates_publishes_no_price_list(self, client):
        """
        Areas and prices are not handed to the storefront. The fee comes from
        where the pin lands, and a table here would invite a guess from the
        address text instead.
        """
        data = (await client.get("/api/v1/delivery/rates")).json()
        assert "regions" not in data
        assert set(data) == {
            "pickup_fee",
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
        # No pin in the request, so no zone and no price. It used to answer
        # with a national default fee; that is gone, and zero would be free
        # delivery handed out for the absence of an address.
        assert response.status_code == 400

    async def test_below_threshold_has_fee(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "subtotal": "100.00",
            },
        )
        assert response.status_code == 400

    async def test_invalid_method_422(self, client):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={"delivery_method": "invalid", "subtotal": "100.00"},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "latitude,longitude",
        [
            # NaN parses as a `Decimal` like any other number and compares
            # false (or, for `Decimal`, raises) against every bound — a
            # coordinate that would otherwise reach the courier as "nowhere in
            # particular" rather than being refused outright. F-COU-11.
            ("NaN", "55.3"),
            ("25.2", "NaN"),
            ("Infinity", "55.3"),
            # Out of the physically possible range for a latitude/longitude.
            ("9999", "55.3"),
            ("25.2", "-9999"),
        ],
    )
    async def test_out_of_range_coordinates_422(self, client, latitude, longitude):
        response = await client.post(
            "/api/v1/delivery/calculate",
            json={
                "delivery_method": "delivery",
                "subtotal": "100.00",
                "latitude": latitude,
                "longitude": longitude,
            },
        )
        assert response.status_code == 422


class TestDeliveryQuote:
    @pytest.mark.parametrize(
        "latitude,longitude",
        [
            ("NaN", "55.3"),
            ("25.2", "NaN"),
            ("Infinity", "55.3"),
            ("9999", "55.3"),
            ("25.2", "-9999"),
        ],
    )
    async def test_out_of_range_coordinates_422(self, client, latitude, longitude):
        """
        F-COU-11: `/quote` calls a courier's live quote API, so an unbounded
        or NaN pin is not just a bad answer — it is a free way to burn the
        courier's own rate limit. Bounds on the request model reject it before
        any courier is asked.
        """
        response = await client.post(
            "/api/v1/delivery/quote",
            json={
                "subtotal": "100.00",
                "latitude": latitude,
                "longitude": longitude,
            },
        )
        assert response.status_code == 422


class TestDeliveryQuoteAbuseRateLimit:
    """
    F-COU-11: `/quote` and `/calculate` are public, unauthenticated, and each
    hit against `/quote` is a call against a courier quote API that throttles
    itself upstream (~100/min) — so an unlimited storefront route is a way for
    one caller to burn the whole account's budget. `/area` already carries
    `@limiter.limit("60/minute")` for the same reason; this locks the other
    two routes to the same ceiling.

    Exercised as a decoration check rather than by firing 61 real requests:
    slowapi's in-memory limiter is a module-level singleton for the life of
    the test process, so driving it over its limit here would also start
    throttling every other test in this file that shares a client IP.
    """

    def test_quote_and_calculate_are_rate_limited(self):
        from app.api.v1 import delivery
        from app.core.limiter import limiter

        for endpoint in (delivery.calculate_delivery, delivery.quote_delivery):
            name = f"{endpoint.__module__}.{endpoint.__name__}"
            limits = limiter._route_limits.get(name, [])
            assert limits, f"{name} carries no rate limit"
            assert [str(lim.limit) for lim in limits] == ["60 per 1 minute"]
