"""The Foodics branch-map seed, and what `integrated_branches` does with it.

The migration is the deploy-time load; `integrated_branches()` is the runtime
read. Together they are the gate on the Foodics master-menu path: an empty map
means no integrated branches, and these two names (once mapped) are the ones
it would return. Karama and DSO stay out — they are not on this Foodics account.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.branch import Branch
from app.services.aggregators import catalog_sync

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "180_foodics_branch_map_seed.py"
)
_spec = importlib.util.spec_from_file_location("foodics_branch_map_seed", _PATH)
migration = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(migration)

FOODICS_BARSHA = "a0371d1d-2971-4ead-9885-e706f01da3d4"
FOODICS_SHARJAH = "a0371d1d-1d1f-40f3-834f-5956b94d7b2b"


class _StubBranch:
    """A branch that answers `has_foodics` the same way the ORM model does."""

    has_foodics = Branch.has_foodics

    def __init__(self, name: str, *, foodics_id: str | None = None) -> None:
        self.name = name
        self.is_active = True
        self.foodics_map = (
            SimpleNamespace(is_active=True, foodics_branch_id=foodics_id)
            if foodics_id
            else None
        )


def _all_kitchens(*, mapped: bool) -> list[_StubBranch]:
    return [
        _StubBranch("Barsha Heights", foodics_id=FOODICS_BARSHA if mapped else None),
        _StubBranch("Sharjah Kitchen", foodics_id=FOODICS_SHARJAH if mapped else None),
        _StubBranch("Al Karama"),
        _StubBranch("Dubai Silicon Oasis"),
    ]


def test_seed_covers_only_the_two_foodics_kitchens():
    names = [name for name, _ in migration._SEED]
    assert names == ["Barsha Heights", "Sharjah Kitchen"]
    assert {fid for _, fid in migration._SEED} == {FOODICS_BARSHA, FOODICS_SHARJAH}


def test_seed_is_a_no_op_unless_the_map_is_empty():
    """The empty table is the guard — not a per-row upsert that would fight
    a human who deleted or remapped a kitchen after the seed ran."""
    src = " ".join(inspect.getsource(migration.upgrade).split())
    assert "NOT EXISTS (SELECT 1 FROM foodics_branch_map)" in src
    assert "ON CONFLICT (branch_id) DO NOTHING" in src
    # One INSERT, so the empty-table check applies to both rows together.
    assert src.count("INSERT INTO foodics_branch_map") == 1


def test_revision_id_fits_alembic_version_column():
    assert len(migration.revision) <= 32
    assert migration.revision == "180_foodics_branch_map_seed"
    assert migration.down_revision == "179_per_area_map_v4"


@pytest.mark.asyncio
async def test_integrated_branches_empty_when_foodics_map_is_empty(mock_db):
    mock_db.execute.return_value.scalars.return_value.all.return_value = _all_kitchens(
        mapped=False
    )
    assert await catalog_sync.integrated_branches(mock_db) == []


@pytest.mark.asyncio
async def test_integrated_branches_sees_barsha_and_sharjah_once_mapped(mock_db):
    kitchens = _all_kitchens(mapped=True)
    mock_db.execute.return_value.scalars.return_value.all.return_value = kitchens
    seen = await catalog_sync.integrated_branches(mock_db)
    assert [b.name for b in seen] == ["Barsha Heights", "Sharjah Kitchen"]
    assert all(b.has_foodics for b in seen)


@pytest.mark.asyncio
async def test_integrated_branches_ignores_an_inactive_map_row(mock_db):
    barsha = _StubBranch("Barsha Heights", foodics_id=FOODICS_BARSHA)
    barsha.foodics_map = SimpleNamespace(
        is_active=False, foodics_branch_id=FOODICS_BARSHA
    )
    mock_db.execute.return_value.scalars.return_value.all.return_value = [barsha]
    assert await catalog_sync.integrated_branches(mock_db) == []
