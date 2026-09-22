from __future__ import annotations

from app.services.aggregators import address_geocoding as geo
from app.services.aggregators import ingest


async def test_normal_sync_does_not_retry_a_terminal_geocoding_outcome(monkeypatch):
    calls = 0

    async def geocode(_address):
        nonlocal calls
        calls += 1
        raise AssertionError("normal sync must not retry a terminal geocoding outcome")

    monkeypatch.setattr(ingest.address_geocoding, "geocode", geocode)
    address, status = await ingest._address_for_upsert(
        "keeta",
        {"address": "Jumeirah 1", "city": "Dubai"},
        None,
        "failed",
        retry_geocoding=False,
    )

    assert address == {"address": "Jumeirah 1", "city": "Dubai"}
    assert status == "failed"
    assert calls == 0


async def test_explicit_backfill_can_retry_a_terminal_geocoding_outcome(monkeypatch):
    async def geocode(address):
        return geo.GeocodingResult(
            {**address, "latitude": 25.2048, "longitude": 55.2708}, "resolved"
        )

    monkeypatch.setattr(ingest.address_geocoding, "geocode", geocode)
    address, status = await ingest._address_for_upsert(
        "keeta",
        {"address": "Jumeirah 1", "city": "Dubai"},
        None,
        "failed",
        retry_geocoding=True,
    )

    assert address["latitude"] == 25.2048
    assert status == "resolved"


async def test_non_keeta_text_address_is_never_sent_to_google(monkeypatch):
    async def geocode(_address):
        raise AssertionError("non-Keeta addresses must not use Google geocoding")

    monkeypatch.setattr(ingest.address_geocoding, "geocode", geocode)
    address, status = await ingest._address_for_upsert(
        "talabat",
        {"address": "Some text", "city": "Dubai"},
        None,
        None,
        retry_geocoding=True,
    )

    assert address == {"address": "Some text", "city": "Dubai"}
    assert status is None


async def test_geocoded_coordinates_survive_a_late_masked_keeta_address(monkeypatch):
    async def geocode(_address):
        return geo.GeocodingResult(
            {"address": "***", "latitude": 25.2048, "longitude": 55.2708},
            "resolved",
        )

    monkeypatch.setattr(ingest.address_geocoding, "geocode", geocode)
    address, status = await ingest._address_for_upsert(
        "keeta",
        {"address": "***"},
        {"address": "Jumeirah 1", "city": "Dubai"},
        None,
        retry_geocoding=True,
    )

    assert address == {
        "address": "Jumeirah 1",
        "city": "Dubai",
        "latitude": 25.2048,
        "longitude": 55.2708,
    }
    assert status == "resolved"


async def test_provider_pin_updates_coordinates_without_replacing_visible_address():
    address, status = await ingest._address_for_upsert(
        "keeta",
        {"address": "***", "latitude": 25.2048, "longitude": 55.2708},
        {
            "address": "Jumeirah 1",
            "city": "Dubai",
            "latitude": 25.2,
            "longitude": 55.2,
        },
        "resolved",
        retry_geocoding=True,
    )

    assert address == {
        "address": "Jumeirah 1",
        "city": "Dubai",
        "latitude": 25.2048,
        "longitude": 55.2708,
    }
    assert status == "provided"
