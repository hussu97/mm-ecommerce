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
        {"address": "Jumeirah 1", "city": "Dubai"},
        None,
        "failed",
        retry_geocoding=True,
    )

    assert address["latitude"] == 25.2048
    assert status == "resolved"
