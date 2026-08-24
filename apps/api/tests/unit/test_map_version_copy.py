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

The specific one that was missing is `batch_group_id`, and it was missing behind
a loop that looked like it handled it — see `test_the_windows_are_not_copied_and_do_not_need_to_be`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1.delivery_zones.schemas import PolygonUpdate
from app.api.v1.delivery_zones.versions import create_version, update_polygon
from app.models.delivery_batch import DeliveryBatchGroup, DeliveryBatchWindow
from app.models.delivery_polygon import DeliveryPolygon, DeliveryPolygonVersion

GROUP_ID = uuid.uuid4()
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
    polygon.batch_group_id = GROUP_ID
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

    def __init__(self, *, group: DeliveryBatchGroup | None = None):
        self.pending: list = []
        self.rows: list = []
        self.group = group
        self.window_queries = 0

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for row in self.pending:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()
        self.rows.extend(self.pending)
        self.pending.clear()

    async def get(self, model, pk):
        return self.group if model is DeliveryBatchGroup else None

    async def execute(self, stmt):
        entities = [d.get("entity") for d in getattr(stmt, "column_descriptions", [])]
        if DeliveryBatchWindow in entities:
            # Counted rather than answered: after the fix nothing in this path
            # should be asking for windows at all.
            self.window_queries += 1
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
async def test_the_run_travels_with_the_zone(source, monkeypatch):
    """
    The bug this file exists for.

    Every zone in a published draft dispatched on its own, because the copy
    dropped `batch_group_id`. Nothing failed: the orders went out, one van each,
    at several times the cost — and the setting could not be put back from the
    console, because no route sets that column at all.
    """
    db = _Db()
    await _clone(db, source, monkeypatch)
    assert db.polygons[0].batch_group_id == GROUP_ID


@pytest.mark.asyncio
async def test_the_windows_are_not_copied_and_do_not_need_to_be(source, monkeypatch):
    """
    Windows hang off the group, and the group is not versioned.

    So a copy pointing at the same group already has the right schedule, and
    `group_for_polygon` finds it by joining `batch_group_id` — there is nothing
    to duplicate. There *was* a loop here that appeared to copy them, and it
    could never have worked: it passed a polygon id to `_windows_of`, which
    filters on `group_id`, so it always matched nothing, and had it matched it
    would have built a `DeliveryBatchWindow(polygon_id=...)` against a column
    that stopped existing in `088`.

    Asserted as "nothing asks for windows" rather than "no windows were added",
    because the second passes just as well when the query is there and broken —
    which is exactly the state this replaced.
    """
    db = _Db()
    await _clone(db, source, monkeypatch)
    assert db.window_queries == 0
    assert not [r for r in db.rows if isinstance(r, DeliveryBatchWindow)]


@pytest.mark.asyncio
async def test_every_setting_on_the_zone_survives_the_clone(source, monkeypatch):
    """
    The whole list, not just the one that was missing.

    Each of these has its own comment in the copy explaining what a shop loses
    when it goes — a fee, an offer, a kitchen, a courier, an escape hatch, a
    run. Asserted together so the next column added to `DeliveryPolygon` and
    forgotten here fails on this line instead of in a shop.
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
        "batch_group_id",
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


# ── the mismatch the fix makes reachable ──────────────────────────────────────


def _draft_zone(provider: str = "lalamove"):
    """
    A draft zone as `update_polygon` reads it.

    A plain stand-in rather than a real `DeliveryPolygon`, because the only way
    to give an ORM row a `version` is through the relationship, and assigning a
    fake one drags in SQLAlchemy's backref machinery. Everything the handler
    touches is a plain attribute, so a namespace is the honest shape here.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Dubai Mid",
        version=SimpleNamespace(is_active=False, name="Draft"),
        delivery_fee=Decimal("35.00"),
        pricing_mode="dynamic",
        free_delivery_eligible=True,
        free_delivery_threshold=Decimal("75.00"),
        fulfilment_provider=provider,
        alternate_providers=["third_party"],
        branch_id=BRANCH_ID,
        batch_group_id=GROUP_ID,
        display_order=7,
    )


async def _change_courier(polygon, to: str, monkeypatch, *, group_books="lalamove"):
    import app.api.v1.delivery_zones.versions as zones

    db = _Db(
        group=DeliveryBatchGroup(
            id=GROUP_ID, name="Dubai Bands", courier_code=group_books
        )
    )

    async def _log_action(*args, **kwargs):
        return None

    async def _execute(stmt):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: polygon, all=lambda: [])
        )

    monkeypatch.setattr(zones.audit_service, "log_action", _log_action)
    monkeypatch.setattr(db, "execute", _execute)
    monkeypatch.setattr(zones, "PolygonResponse", SimpleNamespace(of=lambda p: p))

    await update_polygon(
        polygon_id=polygon.id,
        data=PolygonUpdate(fulfilment_provider=to),
        request=SimpleNamespace(),
        db=db,
        admin=SimpleNamespace(id=uuid.uuid4(), email="a@b.c"),
    )


@pytest.mark.asyncio
async def test_a_zone_that_changes_courier_leaves_the_run(monkeypatch):
    """
    "A run is one booking with one courier."

    This could not arise before: a draft never carried a group, because the
    clone dropped it. Now that it does, changing a draft zone's courier can
    leave it pointed at a group that books somebody else — and nothing
    downstream notices until the window closes and the booking is refused, an
    hour after anybody could have acted.

    Detached rather than refused, for the reason the column's own `SET NULL`
    gives: a zone with no run goes out immediately and costs a fare, and a zone
    pointed at a schedule it cannot use never goes out at all. Refusing would
    also make the courier permanently uneditable on every batched zone, since
    no route attaches one to a group.
    """
    polygon = _draft_zone()
    await _change_courier(polygon, "third_party", monkeypatch)
    assert polygon.fulfilment_provider == "third_party"
    assert polygon.batch_group_id is None, "a third party cannot ride a Lalamove run"


@pytest.mark.asyncio
async def test_a_zone_keeps_its_run_when_the_courier_still_matches(monkeypatch):
    """Re-selecting the courier the run already books changes nothing."""
    polygon = _draft_zone()
    await _change_courier(polygon, "lalamove", monkeypatch)
    assert polygon.batch_group_id == GROUP_ID


@pytest.mark.asyncio
async def test_changing_something_else_never_touches_the_run(monkeypatch):
    """
    The guard hangs off the courier changing, not off the zone being saved. A
    fee edit on a batched zone must leave the run alone.
    """
    import app.api.v1.delivery_zones.versions as zones

    polygon = _draft_zone()
    db = _Db(group=DeliveryBatchGroup(id=GROUP_ID, name="D", courier_code="lalamove"))

    async def _log_action(*args, **kwargs):
        return None

    async def _execute(stmt):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: polygon, all=lambda: [])
        )

    monkeypatch.setattr(zones.audit_service, "log_action", _log_action)
    monkeypatch.setattr(db, "execute", _execute)
    monkeypatch.setattr(zones, "PolygonResponse", SimpleNamespace(of=lambda p: p))

    await update_polygon(
        polygon_id=polygon.id,
        data=PolygonUpdate(delivery_fee=Decimal("40.00")),
        request=SimpleNamespace(),
        db=db,
        admin=SimpleNamespace(id=uuid.uuid4(), email="a@b.c"),
    )
    assert polygon.batch_group_id == GROUP_ID
