"""
What the per-branch priority map claims, pin by pin, and whether it is sane.

`build_delivery_areas` now emits an ordered branch list per area
(`uae_delivery_areas_branch_priority.v1.json`): each polygon names the branches
that can self-serve it, cheapest own-courier first, Sharjah winning ties. That
file feeds a migration, and a migration that ships a bad routing table quietly
sends orders to a branch that cannot make them. So this reads the priority file
and the geometry it is keyed to, resolves the same grid and landmark pins the
single-map comparison uses, and asserts four things:

  1. **Every polygon has somewhere to send an order.** No non-third-party polygon
     may have zero serviceable branches, and every third-party polygon must be a
     single rank-1 row pinned to Sharjah / third_party — the far ground no
     courier is run to.
  2. **Coverage only grows.** Every pin the single-Sharjah assignments file served
     must still resolve to a served polygon. A pin that was served and now is not
     is an address that can no longer be ordered to — printed as COVERAGE LOST.
  3. **Rank follows price.** Within a polygon the own-courier cost must not fall
     as the rank rises — rank 1 is the cheapest branch, rank 2 the next.
  4. **noon Send never crosses an emirate.** A noon-Send rank may only sit on a
     branch whose own emirate is the area's — noon Send cannot cross a boundary,
     so a Dubai branch may not carry a Sharjah area on noon Send, or the reverse.

The grid is the honest half: named landmarks check the places somebody thought
of; the sweep checks the places nobody did, which is where a gap hides.

Run from apps/api:

    python -m scripts.compare_branch_priority
    python -m scripts.compare_branch_priority --step 0.02   # a coarser sweep

Exits non-zero on any hard failure (a violation of 1, 3 or 4, or lost coverage).
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

from app.services.delivery.delivery_zone_service import point_in_geometry
from scripts.build_delivery_areas import (
    BRANCH_REFS,
    CANONICAL,
    DATA,
    OUT_ASSIGN,
    OUT_BRANCH_PRIORITY,
    OUT_GEOMETRY,
    THIRD_PARTY_BRANCH,
)
from scripts.compare_delivery_maps import BBOX, LANDMARKS

#: courier -> the field its price lives under in a branch's cost file. third_party
#: has no fare (nobody is booked), so it is not priced or rank-checked.
COST_FIELD = {
    "slider_bike": "slider_bike",
    "slider_car": "slider_car",
    "noon_send": "noon_send",
}


def _canon(emirate: str) -> str:
    """A branch's `city` / an area emirate as the canonical outline name."""
    return CANONICAL.get(emirate, emirate)


def _load() -> tuple[
    list[dict], dict[str, dict], dict[str, str], dict[str, dict], dict
]:
    """(geometry rows, name->priority, name->label, ref->costs, ref->emirate)."""
    geometry = json.loads(OUT_GEOMETRY.read_text())
    priority = {p["name"]: p for p in json.loads(OUT_BRANCH_PRIORITY.read_text())}

    # name -> label and name -> emirate come from the single-Sharjah assignments,
    # which is also the coverage baseline (every pin it served must stay served).
    assignments = json.loads(OUT_ASSIGN.read_text())
    label_of = {a["name"]: a["label"] for a in assignments}
    emirate_of = {a["name"]: a["emirate"] for a in assignments}

    costs: dict[str, dict] = {}
    branch_emirate: dict[str, str] = {}
    for ref in BRANCH_REFS:
        doc = json.loads((DATA / f"courier_costs.{ref}.json").read_text())
        costs[ref] = doc.get("costs", {})
        branch_emirate[ref] = _canon(doc.get("branch", {}).get("emirate", ""))

    return geometry, priority, label_of, emirate_of, costs, branch_emirate


def _entry_cost(ref: str, label: str, courier: str, costs: dict) -> Decimal | None:
    field = COST_FIELD.get(courier)
    if field is None:
        return None
    value = costs.get(ref, {}).get(label, {}).get(field)
    return None if value is None else Decimal(str(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step",
        type=float,
        default=0.01,
        help="grid spacing in degrees (0.01 ≈ 1.1 km). Default 0.01.",
    )
    args = parser.parse_args()

    geometry, priority, label_of, emirate_of, costs, branch_emirate = _load()
    print(f"{OUT_BRANCH_PRIORITY.name}: {len(priority)} polygons")
    print(f"{OUT_GEOMETRY.name}: {len(geometry)} geometries")
    for ref in BRANCH_REFS:
        print(f"  {ref}: {len(costs[ref])} fares, emirate {branch_emirate[ref]!r}")
    print()

    failures: list[str] = []

    # 1/3/4 — structural, over every polygon in the priority file.
    for name, row in priority.items():
        ranks = row["branch_priority"]
        third_party = row["third_party"]
        label = label_of.get(name, name.split(" · ", 1)[-1])
        area_emirate = emirate_of.get(name, name.split(" · ", 1)[0])

        # (1) third-party shape, and no non-third-party polygon left unserviceable.
        if third_party:
            if not (
                len(ranks) == 1
                and ranks[0]["rank"] == 1
                and ranks[0]["branch_ref"] == THIRD_PARTY_BRANCH
                and ranks[0]["courier"] == "third_party"
            ):
                failures.append(
                    f"THIRD-PARTY SHAPE  {name}: expected one rank-1 "
                    f"{THIRD_PARTY_BRANCH}/third_party, got {ranks}"
                )
            continue
        if not ranks:
            failures.append(f"UNSERVICEABLE      {name}: no serviceable branch")
            continue

        prev: Decimal | None = None
        for entry in ranks:
            # (4) a noon-Send rank must sit in the branch's own emirate — checked
            # independently of the fare, so a stray cross-emirate noon fare is
            # caught as the boundary violation it is, not merely as a missing one.
            if entry["courier"] == "noon_send":
                bemi = branch_emirate.get(entry["branch_ref"], "")
                if area_emirate != bemi:
                    failures.append(
                        f"NOON CROSSES       {name}: rank {entry['rank']} "
                        f"{entry['branch_ref']} noon_send but area is "
                        f"{area_emirate!r}, branch is {bemi!r}"
                    )

            # (3) cost is monotonic non-decreasing with rank.
            cost = _entry_cost(entry["branch_ref"], label, entry["courier"], costs)
            if cost is None:
                failures.append(
                    f"NO FARE            {name}: rank {entry['rank']} "
                    f"{entry['branch_ref']}/{entry['courier']} has no priced fare"
                )
                continue
            if prev is not None and cost < prev:
                failures.append(
                    f"RANK NOT MONOTONIC {name}: rank {entry['rank']} "
                    f"{entry['branch_ref']}/{entry['courier']} {cost} < {prev}"
                )
            prev = cost

    print("Structural checks")
    if failures:
        for line in failures:
            print(f"  {line}")
    else:
        print("  ok: shape, coverage floor, monotonic cost, emirate rule\n")

    # 2 — coverage: every pin the old assignments served must stay served. Same
    # geometry, so a pin resolves to the same polygon name in both; the question
    # is whether that polygon still has somewhere to send an order.
    served = {
        name
        for name, row in priority.items()
        if row["third_party"] or row["branch_priority"]
    }
    old_names = set(label_of)  # every polygon the single-Sharjah file served
    lost: list[tuple[float, float, str]] = []
    checked = 0

    def _resolve(lat: float, lng: float) -> str | None:
        for row in geometry:
            if point_in_geometry(lat, lng, row["geometry"]):
                return row["name"]
        return None

    def compare(lat: float, lng: float, label: str | None = None) -> None:
        nonlocal checked
        checked += 1
        name = _resolve(lat, lng)
        if name is None or name not in old_names:
            return  # sea, desert, or ground the old map did not serve either
        if name not in served:
            lost.append((lat, lng, name))
            if label:
                print(f"  COVERAGE LOST  {label}: {name} → no serviceable branch")

    print("Named landmarks")
    for label, lat, lng in LANDMARKS:
        compare(lat, lng, label)
    print(f"  {len(LANDMARKS)} checked\n")

    min_lat, max_lat, min_lng, max_lng = BBOX
    step = args.step
    cells = 0
    lat = min_lat
    while lat <= max_lat:
        lng = min_lng
        while lng <= max_lng:
            compare(lat, lng)
            cells += 1
            lng += step
        lat += step
    print(f"Grid sweep: {cells} points at {step}°\n")

    print(f"Points the old map served and the new priority map does not: {len(lost)}")
    for lat, lng, name in lost[:20]:
        print(f"  {lat:.4f}, {lng:.4f}  {name}")
    if len(lost) > 20:
        print(f"  ... and {len(lost) - 20} more")

    hard = len(failures) + len(lost)
    print(f"\n{hard} hard failure(s).")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
