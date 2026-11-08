"""
Reordering a menu's groups — and, on the integrator menu, Foodics' categories.

What matters, and is easy to get subtly wrong:

  * the new order must name every live child exactly once, or a stale console
    drops a group to the end without anyone noticing;
  * Foodics keeps a group's child order in its own ``items_index``. Subgroups MM
    does not manage ("New In") must keep their slots, and a categories-only
    rewrite must never detach a subgroup from the Grubtech group — that took
    "New In" off every marketplace once already;
  * the MM order is saved whatever Foodics says; the push only reports.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import BadRequestError
from app.services.catalog import menu_group_service
from app.services.providers import foodics_provider as fp
from app.services.providers.foodics_provider import FoodicsError, merge_items_index

NEW_IN, BROWNIES, COOKIES, MELT, CAKES, HOT, ICED = (
    "new-in",
    "brownies",
    "cookies",
    "melt",
    "cakes",
    "hot",
    "iced",
)
ATTACHED = {NEW_IN, BROWNIES, COOKIES, MELT, CAKES, HOT, ICED}


# ── merge_items_index ────────────────────────────────────────────────────────


def test_unmanaged_subgroups_keep_their_slot():
    current = [NEW_IN, BROWNIES, COOKIES, MELT, CAKES]
    merged = merge_items_index(current, [CAKES, MELT, COOKIES, BROWNIES], ATTACHED)
    assert merged == [NEW_IN, CAKES, MELT, COOKIES, BROWNIES]


def test_categories_foodics_never_indexed_follow_in_mm_order():
    """Hot/Iced Coffee were attached after the index was last saved."""
    current = [NEW_IN, BROWNIES, COOKIES]
    merged = merge_items_index(current, [HOT, ICED, COOKIES, BROWNIES], ATTACHED)
    assert merged == [NEW_IN, HOT, ICED, COOKIES, BROWNIES]


def test_a_category_not_attached_in_foodics_is_not_placed():
    merged = merge_items_index([NEW_IN, BROWNIES], ["ghost", BROWNIES], ATTACHED)
    assert merged == [NEW_IN, BROWNIES]


def test_duplicates_in_the_current_index_cannot_exhaust_the_slots():
    merged = merge_items_index([BROWNIES, NEW_IN, BROWNIES], [BROWNIES], ATTACHED)
    assert merged == [BROWNIES, NEW_IN]


def test_same_order_is_a_no_op():
    current = [NEW_IN, BROWNIES, COOKIES]
    assert merge_items_index(current, [BROWNIES, COOKIES], ATTACHED) == current


# ── FoodicsClient.set_grubtech_category_order ────────────────────────────────


class _FakeFoodics:
    """Answers the Grubtech group GET and records PUTs, applying them like
    Foodics does (an ``items_index`` PUT keeps the subgroups unless told to
    misbehave)."""

    def __init__(self, items_index, *, drop_on_write=None, ignore_index=False):
        self.items_index = list(items_index)
        self.subgroups = sorted(ATTACHED)
        self.puts: list[dict] = []
        self.drop_on_write = drop_on_write
        self.ignore_index = ignore_index

    async def call(self, method, path, params=None, json_body=None):
        if method == "GET":
            return {
                "data": {
                    "id": fp.FOODICS_GRUBTECH_GROUP_ID,
                    "items_index": list(self.items_index),
                    "subgroups": [{"id": s} for s in self.subgroups],
                }
            }
        payload = json_body["payload"]
        self.puts.append(payload)
        if "items_index" in payload and not self.ignore_index:
            self.items_index = payload["items_index"]
        if "subgroups" in payload:
            self.subgroups = [s["id"] for s in payload["subgroups"]]
        elif self.drop_on_write:
            self.subgroups = [s for s in self.subgroups if s != self.drop_on_write]
        return {}


def _client(fake: _FakeFoodics) -> fp.FoodicsClient:
    client = fp.FoodicsClient()
    client._call = fake.call  # type: ignore[method-assign]
    return client


async def test_reorder_writes_the_merged_index():
    fake = _FakeFoodics([NEW_IN, BROWNIES, COOKIES, MELT])
    changed = await _client(fake).set_grubtech_category_order([MELT, BROWNIES, COOKIES])
    assert changed is True
    assert fake.puts == [{"items_index": [NEW_IN, MELT, BROWNIES, COOKIES]}]


async def test_an_unchanged_order_writes_nothing():
    fake = _FakeFoodics([NEW_IN, BROWNIES, COOKIES])
    assert await _client(fake).set_grubtech_category_order([BROWNIES, COOKIES]) is False
    assert fake.puts == []


async def test_a_detached_subgroup_is_restored_and_reported():
    fake = _FakeFoodics([NEW_IN, BROWNIES, COOKIES], drop_on_write=NEW_IN)
    with pytest.raises(FoodicsError, match="detached"):
        await _client(fake).set_grubtech_category_order([COOKIES, BROWNIES])
    assert NEW_IN in fake.subgroups, "the lost subgroup must be put back"


async def test_an_order_foodics_did_not_keep_is_an_error():
    fake = _FakeFoodics([NEW_IN, BROWNIES, COOKIES], ignore_index=True)
    with pytest.raises(FoodicsError, match="did not keep"):
        await _client(fake).set_grubtech_category_order([COOKIES, BROWNIES])


async def test_an_empty_read_never_writes():
    fake = _FakeFoodics([NEW_IN])
    fake.subgroups = []
    with pytest.raises(FoodicsError):
        await _client(fake).set_grubtech_category_order([BROWNIES])
    assert fake.puts == []


# ── menu_group_service.reorder_children ──────────────────────────────────────


def _group(**kw):
    defaults = dict(
        id=uuid.uuid4(),
        parent_id=uuid.uuid4(),
        root_kind="branch",
        display_order=0,
        reference=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def _db(children):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Rows(children))
    return db


async def test_children_are_renumbered_in_the_given_order(monkeypatch):
    parent = _group(parent_id=None)
    a, b, c = _group(display_order=5), _group(display_order=1), _group(display_order=9)
    monkeypatch.setattr(menu_group_service, "get", AsyncMock(return_value=parent))
    db = _db([a, b, c])
    result = await menu_group_service.reorder_children(
        db, parent.id, [c.id, a.id, b.id]
    )
    assert (c.display_order, a.display_order, b.display_order) == (1, 2, 3)
    assert result == {"foodics_status": None, "foodics_detail": None}
    db.flush.assert_awaited()
    db.commit.assert_not_called()


async def test_a_stale_list_is_refused(monkeypatch):
    parent = _group(parent_id=None)
    a, b = _group(), _group()
    monkeypatch.setattr(menu_group_service, "get", AsyncMock(return_value=parent))
    with pytest.raises(BadRequestError, match="changed since"):
        await menu_group_service.reorder_children(_db([a, b]), parent.id, [a.id])
    with pytest.raises(BadRequestError, match="more than once"):
        await menu_group_service.reorder_children(
            _db([a, b]), parent.id, [a.id, a.id, b.id]
        )


async def test_the_integrator_root_pushes_its_category_order(monkeypatch):
    parent = _group(parent_id=None, root_kind="integrator")
    a = _group(root_kind="integrator", reference=COOKIES)
    b = _group(root_kind="integrator", reference=BROWNIES)
    c = _group(root_kind="integrator", reference=None)
    monkeypatch.setattr(menu_group_service, "get", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        "app.core.config.settings.CATALOG_SYNC_ENABLED", True, raising=False
    )
    monkeypatch.setattr(fp.FoodicsClient, "is_configured", property(lambda self: True))
    push = AsyncMock(return_value=True)
    monkeypatch.setattr(fp.provider, "set_grubtech_category_order", push)
    result = await menu_group_service.reorder_children(
        _db([a, b, c]), parent.id, [b.id, c.id, a.id]
    )
    push.assert_awaited_once_with([BROWNIES, COOKIES])
    assert result["foodics_status"] == "updated"


async def test_a_foodics_failure_is_reported_not_raised(monkeypatch):
    parent = _group(parent_id=None, root_kind="integrator")
    a = _group(root_kind="integrator", reference=COOKIES)
    monkeypatch.setattr(menu_group_service, "get", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        "app.core.config.settings.CATALOG_SYNC_ENABLED", True, raising=False
    )
    monkeypatch.setattr(fp.FoodicsClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        fp.provider,
        "set_grubtech_category_order",
        AsyncMock(side_effect=FoodicsError("console down")),
    )
    result = await menu_group_service.reorder_children(_db([a]), parent.id, [a.id])
    assert result == {"foodics_status": "failed", "foodics_detail": "console down"}
    assert a.display_order == 1, "the MM order is saved regardless"


async def test_writes_switched_off_skip_foodics(monkeypatch):
    parent = _group(parent_id=None, root_kind="integrator")
    a = _group(root_kind="integrator", reference=COOKIES)
    monkeypatch.setattr(menu_group_service, "get", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        "app.core.config.settings.CATALOG_SYNC_ENABLED", False, raising=False
    )
    push = AsyncMock()
    monkeypatch.setattr(fp.provider, "set_grubtech_category_order", push)
    result = await menu_group_service.reorder_children(_db([a]), parent.id, [a.id])
    assert result["foodics_status"] == "skipped"
    push.assert_not_awaited()
