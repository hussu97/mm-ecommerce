"""The GrubOps→external_item_map merge: the column mapping the live paths depend on.

GrubOps' item map was folded into the generalized `external_item_map`. The matcher
now writes ExternalItemMap rows, and the order-ingest resolver / OOS push read them.
These pin the 1:1 column mapping (brand→scope, recipe→external_ref,
modifier→external_sub_ref, type→external_type) that keeps the live behaviour
unchanged, since the existing suite mocks those paths.
"""

from __future__ import annotations

import uuid

from app.models.external_item_map import ExternalItemMap
from app.services.grubops.grubops_mapping import Candidate, SyncSummary, _upsert


class _CaptureDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_matcher_writes_a_recipe_as_a_grubops_product_row():
    db = _CaptureDB()
    pid = uuid.uuid4()
    recipe = Candidate(
        item_id="r1", name="Pistachio Kunafa", brand_id="brand-9", grubops_type="RECIPE"
    )

    _upsert(
        db,
        None,
        SyncSummary(),
        kind="product",
        product_id=pid,
        option_id=None,
        candidate=recipe,
        score=1.0,
        method="exact",
    )

    row = db.added[0]
    assert isinstance(row, ExternalItemMap)
    assert row.system == "grubops"
    assert row.mm_kind == "product"
    assert row.product_id == pid
    assert row.modifier_option_id is None
    assert row.scope == "brand-9"  # brand → scope
    assert row.external_ref == "r1"  # recipe id is the primary key
    assert row.external_sub_ref is None
    assert row.external_type == "RECIPE"
    assert row.external_name == "Pistachio Kunafa"
    assert row.approved is False


def test_matcher_writes_a_modifier_pinned_to_its_recipe():
    db = _CaptureDB()
    oid = uuid.uuid4()
    modifier = Candidate(
        item_id="m1",
        name="3 Pieces",
        brand_id="brand-9",
        grubops_type="MODIFIER",
        parent_recipe_id="r1",
    )

    _upsert(
        db,
        None,
        SyncSummary(),
        kind="option",
        product_id=None,
        option_id=oid,
        candidate=modifier,
        score=0.9,
        method="fuzzy",
    )

    row = db.added[0]
    assert row.system == "grubops"
    assert row.mm_kind == "option"
    assert row.modifier_option_id == oid
    assert row.product_id is None
    # A modifier keeps its recipe (external_ref) AND its own id (external_sub_ref):
    # the modifier id alone is ambiguous, so the resolver keys options on sub_ref.
    assert row.external_ref == "r1"
    assert row.external_sub_ref == "m1"
    assert row.external_type == "MODIFIER"


def test_matcher_refreshes_name_only_on_an_existing_row():
    """Re-running the matcher never overrules a human — only the display name is
    refreshed, ids and approval are left as they are."""
    db = _CaptureDB()
    existing = ExternalItemMap(
        system="grubops",
        mm_kind="product",
        external_ref="r1",
        external_name="Old Name",
        external_type="RECIPE",
        match_method="manual",
        approved=True,
    )
    summary = SyncSummary()
    _upsert(
        db,
        existing,
        summary,
        kind="product",
        product_id=uuid.uuid4(),
        option_id=None,
        candidate=Candidate(
            item_id="r1", name="New Name", brand_id="b", grubops_type="RECIPE"
        ),
        score=1.0,
        method="exact",
    )
    assert db.added == []  # nothing inserted
    assert existing.external_name == "New Name"  # only the name refreshed
    assert existing.approved is True  # approval untouched
    assert existing.match_method == "manual"  # a human's correction stands
    assert summary.refreshed == 1
