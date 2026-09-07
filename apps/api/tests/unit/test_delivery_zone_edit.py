"""Editing a zone in place: what the cache and the threshold column demand.

Two findings from the 2026-09-06 courier audit meet here, both on the in-place
edit path (`PUT /delivery-zones/polygons/{id}`):

* F-COU-9 — the active map's parsed zones are cached in-process, per worker. The
  cache was keyed by version id alone, and an in-place edit does not mint a new
  version, so only the worker that served the edit saw it; the rest kept quoting
  the old fee. The fix stamps a `revision` on the version, bumped by the edit and
  folded into the cache key, so every worker re-reads on its next quote.

* F-COU-10 — `free_delivery_threshold` is NOT NULL, but the handler let an
  explicit `null` through, which flushed NULL into the column and, short of that,
  built `float(None)` into the audit payload — either way a 500. `null` now means
  "leave it", like every other field on the update.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.sql.dml import Update

import app.api.v1.delivery_zones.versions as zones
from app.models.delivery_polygon import DeliveryPolygon, DeliveryPolygonVersion
from app.services.delivery import delivery_zone_service

VERSION_ID = uuid.uuid4()


# ── F-COU-9: the cache picks up an edit without anyone busting it ──────────────


def _version(revision: int) -> DeliveryPolygonVersion:
    return DeliveryPolygonVersion(
        id=VERSION_ID, name="Live", is_active=True, revision=revision
    )


def _polygon(fee: str) -> DeliveryPolygon:
    return DeliveryPolygon(
        id=uuid.uuid4(),
        version_id=VERSION_ID,
        name="Sharjah Central",
        delivery_fee=Decimal(fee),
        pricing_mode="static",
        free_delivery_eligible=False,
        free_delivery_threshold=Decimal("150.00"),
        fulfilment_provider="noon_send",
        geometry={
            "type": "Polygon",
            "coordinates": [[[55.0, 25.0], [55.5, 25.0], [55.5, 25.5], [55.0, 25.5]]],
        },
        min_lat=Decimal("25.0"),
        max_lat=Decimal("25.5"),
        min_lng=Decimal("55.0"),
        max_lng=Decimal("55.5"),
        display_order=0,
    )


class _ZoneReadDb:
    """A session that answers the two reads `get_active_zones` makes.

    The active-version lookup and the polygon fetch are told apart by what each
    SELECT is for; the values it hands back are whatever the test has set, so a
    test can move the map underneath a reader between two calls — which is what a
    second worker committing an edit looks like to the first.
    """

    def __init__(self, version, polygons):
        self.version = version
        self.polygons = polygons

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is DeliveryPolygonVersion:
            rows = [self.version] if self.version and self.version.is_active else []
            return SimpleNamespace(
                scalars=lambda rows=rows: SimpleNamespace(first=lambda: rows[0] if rows else None)
            )
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(self.polygons))
        )


@pytest.mark.asyncio
async def test_a_fee_edit_is_seen_by_a_reader_that_never_invalidated():
    """The heart of F-COU-9, at the level the bug lived on.

    One reader caches the live map, another worker edits a fee and bumps the
    version's revision, and the first reader — which never calls
    `invalidate_cache` — must still quote the new fee, because the revision in
    its cache key no longer matches the one on the version row.
    """
    delivery_zone_service.invalidate_cache()
    db = _ZoneReadDb(_version(0), [_polygon("15.00")])

    before = await delivery_zone_service.get_active_zones(db)
    assert before[0].delivery_fee == Decimal("15.00")

    # Another worker edited the fee and bumped the revision in the same commit.
    # This reader is handed the new map and the new revision, but is never told
    # to clear anything.
    db.version = _version(1)
    db.polygons = [_polygon("25.00")]

    after = await delivery_zone_service.get_active_zones(db)
    assert after[0].delivery_fee == Decimal("25.00")


@pytest.mark.asyncio
async def test_an_unchanged_revision_is_still_served_from_cache():
    """The stamp only forces a re-read when it moved — a matching revision is a
    hit, so the cache still earns its keep on the quotes between edits."""
    delivery_zone_service.invalidate_cache()
    parsed_from: list[str] = []

    class _CountingDb(_ZoneReadDb):
        async def execute(self, stmt):
            entity = stmt.column_descriptions[0]["entity"]
            if entity is DeliveryPolygon:
                parsed_from.append("db")
            return await super().execute(stmt)

    db = _CountingDb(_version(3), [_polygon("15.00")])
    await delivery_zone_service.get_active_zones(db)
    await delivery_zone_service.get_active_zones(db)

    # The polygons were fetched once; the second quote came from the cache.
    assert parsed_from == ["db"]


# ── the edit path bumps the revision, and null leaves the threshold alone ──────


class _EditDb:
    """Enough session for `update_polygon`: hand back the polygon, model the
    atomic revision bump as a mutation of the in-memory version."""

    def __init__(self, polygon):
        self.polygon = polygon
        self.updates: list[Update] = []

    async def execute(self, stmt):
        if isinstance(stmt, Update):
            self.updates.append(stmt)
            self.polygon.version.revision += 1
            return SimpleNamespace(rowcount=1)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: self.polygon)
        )

    async def flush(self):
        return None

    async def get(self, _model, _pk):
        return None


def _live_polygon() -> DeliveryPolygon:
    polygon = _polygon("15.00")
    polygon.version = _version(0)
    polygon.alternate_providers = []
    return polygon


async def _call_update(db, data, monkeypatch):
    captured: dict = {}

    async def _log_action(*_args, **kwargs):
        # The payload is built by `update_polygon` as this call's arguments, so
        # a `float(None)` in it would raise here, before this stub runs.
        captured.update(kwargs)
        return None

    monkeypatch.setattr(zones.audit_service, "log_action", _log_action)
    monkeypatch.setattr(zones, "PolygonResponse", SimpleNamespace(of=lambda p: p))
    await zones.update_polygon(
        polygon_id=db.polygon.id,
        data=data,
        request=SimpleNamespace(),
        db=db,
        admin=SimpleNamespace(id=uuid.uuid4(), email="a@b.c"),
    )
    return captured


@pytest.mark.asyncio
async def test_editing_a_zone_bumps_the_version_revision(monkeypatch):
    """F-COU-9 on the write side: the edit increments the version's revision, in
    the same transaction, so the readers above have something to notice."""
    db = _EditDb(_live_polygon())
    data = zones.PolygonUpdate(delivery_fee=Decimal("25.00"))

    await _call_update(db, data, monkeypatch)

    assert db.polygon.delivery_fee == Decimal("25.00")
    assert db.polygon.version.revision == 1
    assert len(db.updates) == 1


@pytest.mark.asyncio
async def test_a_null_threshold_leaves_the_existing_value_untouched(monkeypatch):
    """F-COU-10: `free_delivery_threshold: null` must not 500.

    The column is NOT NULL, so writing the null through blew up on the flush; and
    even where it did not, `float(None)` in the audit payload did. `null` now
    means "leave it", so the stored value survives and the payload is built from
    a real number.
    """
    db = _EditDb(_live_polygon())
    original = db.polygon.free_delivery_threshold
    data = zones.PolygonUpdate.model_validate({"free_delivery_threshold": None})
    assert "free_delivery_threshold" in data.model_fields_set  # an explicit null

    captured = await _call_update(db, data, monkeypatch)

    # Preserved, not nulled.
    assert db.polygon.free_delivery_threshold == original
    # And the audit payload was built from the real value, not `float(None)`.
    assert captured["changes"]["to"]["free_delivery_threshold"] == float(original)
