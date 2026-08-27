"""Unit coverage for the external item-map resolver's pure logic."""

from __future__ import annotations

from app.services.catalog import external_item_map_service as svc


def test_normalize_ref_lowercases_and_collapses_space():
    assert svc.normalize_ref("  Basque   Cheesecake ") == "basque cheesecake"
    assert svc.normalize_ref("Brookie Cookie Melt (500 grams)") == (
        "brookie cookie melt (500 grams)"
    )


def test_normalize_ref_none_for_blank():
    assert svc.normalize_ref(None) is None
    assert svc.normalize_ref("") is None
    assert svc.normalize_ref("   ") is None


def test_normalize_ref_is_stable_across_spacing_and_case():
    # Two spellings of the same item collapse to one key, so one map row serves both.
    assert svc.normalize_ref("NUTELLA cookie melt") == svc.normalize_ref(
        "nutella  Cookie Melt"
    )
