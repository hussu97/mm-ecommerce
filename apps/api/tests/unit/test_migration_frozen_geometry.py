"""
A migration may not read a file that something else rewrites.

Migrations 057, 059 and 065 each seeded a delivery map by reading
`app/data/uae_delivery_zones.geojson.json` — the file
`scripts/build_delivery_zones.py` writes. That worked until the map was redrawn
with different zone names, at which point every one of them started raising
`No geometry for delivery zones: ['Dubai City', 'Sharjah City']` and a database
could no longer be built from empty. CI does exactly that on every merge, and
so does anyone setting the project up for the first time.

It is a subtle failure because nothing is wrong with either file. The migration
is fine, the geometry is fine; they simply describe different worlds, and the
migration has no way to say which world it was written for. A migration is a
historical record — it has to keep meaning in five years what it meant on the
day it was written, and it cannot do that while pointing at a moving target.

So the rule is: **the generator writes `uae_delivery_zones.geojson.json`, and
migrations read a numbered snapshot of it.** Redrawing the map means copying the
new output to the next `.vN.` and pointing only the new migration at it.

This has now caught the same mistake twice — `.v1.` was frozen when the map
changed at 057, the lesson was not written down anywhere a person would find it,
and the identical break happened again at 085. Hence a test rather than a
comment.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
DATA = Path(__file__).resolve().parents[2] / "app" / "data"

#: What the generator writes. Anything reading this by name is reading whatever
#: the map happens to look like today, not what it looked like when the
#: migration was authored.
MUTABLE = "uae_delivery_zones.geojson.json"

#: A frozen copy: `uae_delivery_zones.v3.geojson.json` and friends.
FROZEN = re.compile(r"uae_delivery_zones\.v\d+\.geojson\.json")

#: Every geometry filename a migration mentions.
ANY_ZONE_FILE = re.compile(r"uae_delivery_zones[a-z0-9.]*\.json")


def _migrations() -> list[Path]:
    return sorted(p for p in VERSIONS.glob("*.py") if p.name != "__init__.py")


def test_no_migration_reads_the_generators_output() -> None:
    offenders: list[str] = []
    for path in _migrations():
        for name in set(ANY_ZONE_FILE.findall(path.read_text())):
            if name == MUTABLE:
                offenders.append(path.name)
    assert not offenders, (
        f"{sorted(set(offenders))} read {MUTABLE}, which "
        "`scripts/build_delivery_zones.py` overwrites. Copy the generated file "
        "to the next `uae_delivery_zones.vN.geojson.json` and read that instead "
        "— otherwise redrawing the map breaks `alembic upgrade head` on an empty "
        "database, and the break only shows up on a fresh install or in CI."
    )


def test_every_frozen_snapshot_a_migration_asks_for_exists() -> None:
    """
    A migration naming a snapshot nobody committed fails at the worst moment —
    part-way through `upgrade head`, on a database that is already half migrated.
    """
    missing: list[tuple[str, str]] = []
    for path in _migrations():
        for name in sorted(set(FROZEN.findall(path.read_text()))):
            if not (DATA / name).exists():
                missing.append((path.name, name))
    assert not missing, f"missing geometry snapshots: {missing}"


def test_the_snapshots_are_real_zone_files() -> None:
    """Guards against an empty or truncated copy, which would fail the same way
    a missing one does but with a stranger message."""
    import json

    for path in sorted(DATA.glob("uae_delivery_zones.v*.geojson.json")):
        zones = json.loads(path.read_text())
        assert isinstance(zones, list) and zones, f"{path.name} holds no zones"
        for zone in zones:
            assert zone.get("name"), f"{path.name} has a zone with no name"
            assert zone.get("geometry", {}).get("coordinates"), (
                f"{path.name}: {zone.get('name')} has no coordinates"
            )
