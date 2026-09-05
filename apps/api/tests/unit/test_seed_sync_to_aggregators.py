"""The `sync_to_aggregators` seed — the set it turns on and its guardrails.

`build_mm_menu` reads `sync_to_aggregators == True` as the desired menu, so this
seed defines what the pipeline pushes. It must be "what is live on the marketplaces
today" (the 46 Grubtech price-tag SKUs + FG0052 Brookies), match by the exact `false`
value so it cannot fight the admin, and fit the 32-char alembic revision column.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "181_seed_sync_to_aggregators.py"
)
_spec = importlib.util.spec_from_file_location("seed_sync_to_aggregators", _PATH)
migration = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(migration)


def test_revision_fits_alembic_column_and_chains_head():
    assert migration.revision == "181_seed_sync_to_aggregators"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "180_foodics_branch_map_seed"


def test_seed_set_is_the_live_marketplace_set():
    skus = set(migration._SKUS)
    # 45 = Grubtech tag (46) − seasonal boxes {FG0118, FG0127, FG0128} + Brookies
    # (FG0052) + Gift Note Card (FG0050).
    assert len(migration._SKUS) == 45
    assert len(skus) == 45  # no duplicates
    assert "FG0052" in skus  # Brookies coverage gap included
    assert "FG0050" in skus  # Gift Note Card meant to be everywhere
    # Seasonal boxes must NOT sync (deactivate on every aggregator).
    assert skus.isdisjoint({"FG0118", "FG0127", "FG0128"})
    # The ₿15 "Single" SKUs (FG0001..FG0017) are still not live on the marketplaces.
    assert skus.isdisjoint({f"FG{n:04d}" for n in range(1, 18)})


def test_upgrade_only_flips_rows_that_are_still_false():
    # The guard is the exact value it replaces, so a human toggle is never fought.
    import inspect

    src = inspect.getsource(migration.upgrade)
    assert "sync_to_aggregators = false" in src
    assert "sync_to_aggregators = true" in src
