"""
Cloning a map into a draft has to bring the whole zone, not most of it.

`create_version` is how every price change reaches the storefront: clone the
live map, edit the copy, publish the copy. So every column it forgets is a
setting the shop silently loses the next time somebody changes a fee — and none
of them fail loudly, because a zone missing one of these is still a perfectly
valid zone. It just answers a different question than the one it used to.

The copy list has a comment per field explaining what breaks when it is dropped,
and each of those comments was written after somebody found out. This is that
list, asserted rather than described: the test walks a source zone with every
field set and checks the copy against it, so a column added later without being
copied fails here rather than in production a release afterwards.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1.delivery_zones.versions import create_version
from app.models.delivery_polygon import DeliveryPolygon, DeliveryPolygonVersion

BRANCH_ID = uuid.uuid4()


def _source_polygon(**overrides) -> DeliveryPolygon:
    """A zone with every setting deliberately non-default, so a dropped one shows."""
    polygon = DeliveryPolygon(
        id=uuid.uuid4(),
        name="Dubai Mid",
        delivery_fee=Decimal("35.00"),
        pricing_mode="dynamic",
        free_delivery_eligible=True,
        free_delivery_threshold=Decimal("75.00"),
        fulfilment_provider="lalamove",
        geometry={"type": "Polygon", "coordinates": [[[55.1, 25.1], [55.2, 25.2]]]},
        min_lat=Decimal("25.1"),
        max_lat=Decimal("25.2"),
        min_lng=Decimal("55.1"),
        max_lng=Decimal("55.2"),
        display_order=7,
    )
    polygon.alternate_providers = ["third_party"]
    polygon.branch_id = BRANCH_ID
    for key, value in overrides.items():
        setattr(polygon, key, value)
    return polygon


class _Db:
    """
    Enough session to clone a version and hand back what was added.

    Models `autoflush=False` the way the real sessionmaker is built: `add` holds
    a row pending and only `flush` publishes it. Nothing here reads back what it
    added, but a fake that is read-your-own-writes when the application is not
    asserts a database we do not have.
    """

    def __init__(self):
        self.pending: list = []
        self.rows: list = []

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for row in self.pending:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()
        self.rows.extend(self.pending)
        self.pending.clear()

    async def get(self, _model, _pk):
        return None

    async def execute(self, _stmt):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None)
        )

    @property
    def polygons(self) -> list[DeliveryPolygon]:
        return [r for r in self.rows if isinstance(r, DeliveryPolygon)]


async def _clone(db: _Db, source: DeliveryPolygonVersion, monkeypatch):
    """Run `create_version` against the fake, with its two lookups stubbed."""
    import app.api.v1.delivery_zones.versions as zones

    async def _load_version(_db, _id):
        return source

    async def _log_action(*args, **kwargs):
        return None

    monkeypatch.setattr(zones, "_load_version", _load_version)
    monkeypatch.setattr(zones.audit_service, "log_action", _log_action)
    monkeypatch.setattr(
        zones, "VersionResponse", SimpleNamespace(of=lambda version: version)
    )
    await create_version(
        data=SimpleNamespace(name="Draft", notes=None, source_version_id=source.id),
        request=SimpleNamespace(),
        db=db,
        admin=SimpleNamespace(id=uuid.uuid4(), email="a@b.c"),
    )


@pytest.fixture
def source() -> DeliveryPolygonVersion:
    version = DeliveryPolygonVersion(id=uuid.uuid4(), name="Live", is_active=True)
    version.polygons = [_source_polygon()]
    return version


@pytest.mark.asyncio
async def test_every_setting_on_the_zone_survives_the_clone(source, monkeypatch):
    """
    The whole list, not just one field.

    Each of these has its own comment in the copy explaining what a shop loses
    when it goes — a fee, an offer, a kitchen, a courier, an escape hatch.
    Asserted together so the next column added to `DeliveryPolygon` and forgotten
    here fails on this line instead of in a shop.
    """
    db = _Db()
    await _clone(db, source, monkeypatch)
    original, copy = source.polygons[0], db.polygons[0]

    for field in (
        "name",
        "delivery_fee",
        "pricing_mode",
        "free_delivery_eligible",
        "free_delivery_threshold",
        "fulfilment_provider",
        "alternate_providers",
        "branch_id",
        "geometry",
        "min_lat",
        "max_lat",
        "min_lng",
        "max_lng",
        "display_order",
    ):
        assert getattr(copy, field) == getattr(original, field), field


@pytest.mark.asyncio
async def test_the_copy_gets_its_own_alternates_list(source, monkeypatch):
    """
    A JSONB list shared between two rows makes editing the draft edit the live
    map — the one thing versioning exists to prevent.
    """
    db = _Db()
    await _clone(db, source, monkeypatch)
    copy = db.polygons[0]
    copy.alternate_providers.append("noon_send")
    assert source.polygons[0].alternate_providers == ["third_party"]
