from __future__ import annotations

from decimal import Decimal

from app.services.aggregators import address_geocoding as geo


def test_address_text_keeps_every_usable_address_part_and_adds_country():
    assert (
        geo.address_text(
            {
                "address": "Al Barsha South",
                "building": "Villa 14",
                "unit": "Flat 2",
                "city": "Dubai",
            }
        )
        == "Al Barsha South, Dubai, Villa 14, Flat 2, United Arab Emirates"
    )


async def test_geocode_never_calls_google_when_the_channel_already_supplied_a_uae_pin(
    monkeypatch,
):
    class Client:
        def __init__(self, **_kwargs):
            raise AssertionError("a supplied pin must not spend a geocoding request")

    monkeypatch.setattr(geo.httpx, "AsyncClient", Client)
    result = await geo.geocode({"lat": 25.2048, "lng": 55.2708, "city": "Dubai"})

    assert result.status == "provided"
    assert result.address["lat"] == 25.2048


async def test_geocode_normalises_a_provider_e7_pin_without_calling_google(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            raise AssertionError("a supplied E7 pin must not spend a geocoding request")

    monkeypatch.setattr(geo.httpx, "AsyncClient", Client)
    result = await geo.geocode({"lat": "250466655", "lng": "552053000"})

    assert result.status == "provided"
    assert result.address == {"lat": 25.0466655, "lng": 55.2053}


async def test_geocode_adds_a_uae_pin_and_biases_the_request_to_uae(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [{"location": {"latitude": 25.2048, "longitude": 55.2708}}]
            }

    class Client:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, params, headers):
            seen["url"] = url
            seen["params"] = params
            seen["headers"] = headers
            return Response()

    async def access_token():
        return "vm-oauth-token"

    monkeypatch.setattr(
        geo.settings, "GOOGLE_GEOCODING_BILLING_PROJECT", "maps-billing"
    )
    monkeypatch.setattr(geo, "_access_token", access_token)
    monkeypatch.setattr(geo.httpx, "AsyncClient", Client)

    result = await geo.geocode(
        {"address": "Jumeirah 1", "building": "Villa 7", "city": "Dubai"}
    )

    assert result.status == "resolved"
    assert result.address["latitude"] == 25.2048
    assert result.address["longitude"] == 55.2708
    assert seen["url"] == "https://geocode.googleapis.com/v4/geocode/address"
    assert seen["params"]["address.regionCode"] == "AE"
    assert seen["params"]["regionCode"] == "AE"
    assert (
        seen["params"]["address.addressLines"]
        == "Jumeirah 1, Dubai, Villa 7, United Arab Emirates"
    )
    assert seen["headers"]["Authorization"] == "Bearer vm-oauth-token"
    assert seen["headers"]["X-Goog-User-Project"] == "maps-billing"
    assert seen["headers"]["X-Goog-FieldMask"] == "results.location"


async def test_geocode_records_out_of_country_result_without_a_retryable_pin(
    monkeypatch,
):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [{"location": {"latitude": 51.5072, "longitude": -0.1276}}]
            }

    class Client:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    async def access_token():
        return "vm-oauth-token"

    monkeypatch.setattr(
        geo.settings, "GOOGLE_GEOCODING_BILLING_PROJECT", "maps-billing"
    )
    monkeypatch.setattr(geo, "_access_token", access_token)
    monkeypatch.setattr(geo.httpx, "AsyncClient", Client)

    result = await geo.geocode({"address": "Uncertain address"})

    assert result.status == "outside_uae"
    assert "latitude" not in result.address
    assert (
        geo.coordinates({"latitude": Decimal("51.5"), "longitude": Decimal("-0.1")})
        is None
    )


async def test_geocode_skips_the_request_until_a_billing_project_is_configured(
    monkeypatch,
):
    class Client:
        def __init__(self, **_kwargs):
            raise AssertionError("missing configuration must not call Google")

    monkeypatch.setattr(geo.settings, "GOOGLE_GEOCODING_BILLING_PROJECT", "")
    monkeypatch.setattr(geo.httpx, "AsyncClient", Client)

    result = await geo.geocode({"address": "Jumeirah 1", "city": "Dubai"})

    assert result.status == "not_configured"


async def test_access_token_reuses_a_valid_vm_token_without_refreshing(monkeypatch):
    class Credentials:
        valid = True
        token = "cached-vm-token"

        def refresh(self, _request):
            raise AssertionError("a still-valid VM token should be reused")

    monkeypatch.setattr(
        geo.settings, "GOOGLE_GEOCODING_BILLING_PROJECT", "maps-billing"
    )
    monkeypatch.setattr(geo, "_credentials", Credentials())

    assert await geo._access_token() == "cached-vm-token"
