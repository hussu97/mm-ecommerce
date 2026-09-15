"""Unit tests for the catalog-mapping fold-fallback index."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.external_item_map import METHOD_EXACT, METHOD_FUZZY, METHOD_MANUAL
from app.services.aggregators.catalog_mapping import (
    KIND_PRODUCT,
    _fold_index,
    _upsert,
)


def test_fold_index_matches_ampersand_and_plural():
    from app.services.aggregators.catalog_diff import normalize_name

    idx = _fold_index(
        [
            (1, "Dark Chocolate and Walnut Brownie"),
            (2, "Chocolate & Whipped Salted Caramel Cake Slice"),
        ]
    )
    # channel names differing only by & / plural fold to the same MM key
    assert idx[normalize_name("Dark Chocolate & Walnut Brownies")] == 1
    assert idx[normalize_name("Chocolate And Whipped Salted Caramel Cake Slice")] == 2


def test_fold_index_drops_ambiguous_keys():
    # Two distinct names that collapse to the same fold key must be dropped so a
    # fallback never mis-maps.
    from app.services.aggregators.catalog_diff import normalize_name

    idx = _fold_index([(1, "Cookie"), (2, "Cookies")])
    assert normalize_name("Cookie") == normalize_name("Cookies")
    assert normalize_name("Cookie") not in idx  # ambiguous → dropped


def test_fold_index_keeps_unambiguous():
    idx = _fold_index([(1, "Basque Cheesecake"), (2, "Chocolate Mousse")])
    from app.services.aggregators.catalog_diff import normalize_name

    assert idx[normalize_name("Basque Cheesecakes")] == 1  # plural still maps
    assert idx[normalize_name("Chocolate Mousse")] == 2


# ── _upsert approval/method (F-AGG-20) ────────────────────────────────────────
#
# A fold (fuzzy) match must never be auto-approved as a manual mapping — a
# plural/singular collision would point one item's stock at the wrong product.


class _UpsertDb:
    def __init__(self, existing=None):
        self._existing = existing
        self.added = []
        self.flushed = 0

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


@pytest.mark.asyncio
async def test_upsert_fold_match_is_unapproved_fuzzy():
    db = _UpsertDb()
    approved = await _upsert(
        db,
        system="careem",
        external_ref="Cookies",
        external_name="Cookies",
        mm_kind=KIND_PRODUCT,
        product_id=1,
        approve=False,
        method=METHOD_FUZZY,
    )
    assert approved is False
    row = db.added[0]
    assert not row.approved
    assert row.match_method == METHOD_FUZZY


@pytest.mark.asyncio
async def test_upsert_exact_match_is_approved_manual():
    db = _UpsertDb()
    approved = await _upsert(
        db,
        system="careem",
        external_ref="Basque Cheesecake",
        external_name="Basque Cheesecake",
        mm_kind=KIND_PRODUCT,
        product_id=1,
        approve=True,
        method=METHOD_EXACT,
    )
    assert approved is True
    row = db.added[0]
    assert row.approved
    assert row.match_method == METHOD_MANUAL
