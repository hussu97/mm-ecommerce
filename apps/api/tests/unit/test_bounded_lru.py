"""The bounded process cache behind the courier/zone quote caches (F-COU-21)."""

from __future__ import annotations

import pytest

from app.core.bounded import BoundedLRU


def test_it_evicts_the_oldest_key_past_the_cap():
    c: BoundedLRU[str, int] = BoundedLRU(2)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3  # pushes "a" out
    assert "a" not in c
    assert dict(c) == {"b": 2, "c": 3}


def test_a_read_marks_a_key_recently_used_so_it_survives():
    c: BoundedLRU[str, int] = BoundedLRU(2)
    c["a"] = 1
    c["b"] = 2
    assert c.get("a") == 1  # "a" is now most-recent
    c["c"] = 3  # so "b" is the oldest and goes, not "a"
    assert "b" not in c
    assert set(c) == {"a", "c"}


def test_rewriting_a_key_does_not_grow_the_map_and_refreshes_recency():
    c: BoundedLRU[str, int] = BoundedLRU(2)
    c["a"] = 1
    c["b"] = 2
    c["a"] = 10  # same key, updated + moved to most-recent
    assert len(c) == 2
    c["c"] = 3  # "b" is oldest now
    assert "b" not in c
    assert c["a"] == 10


def test_get_returns_the_default_for_a_missing_key():
    c: BoundedLRU[str, int] = BoundedLRU(2)
    assert c.get("nope") is None
    assert c.get("nope", -1) == -1


def test_clear_empties_it():
    c: BoundedLRU[str, int] = BoundedLRU(2)
    c["a"] = 1
    c.clear()
    assert len(c) == 0


def test_a_nonsense_cap_is_refused():
    with pytest.raises(ValueError):
        BoundedLRU(0)
