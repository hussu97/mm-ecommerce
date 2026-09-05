"""Unit tests for the catalog-mapping fold-fallback index."""

from __future__ import annotations

from app.services.aggregators.catalog_mapping import _fold_index


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
