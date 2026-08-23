"""
What the new delivery map changes, pin by pin, against the one in force.

Redrawing zones is the one change in this codebase that can move a price
without anybody editing a price. Every band inherits its parent's fee by
convention, and a convention is not a guarantee — so this reads both maps as
the migrations seed them, resolves the same points through both, and prints
every difference it finds.

Two kinds of difference, and they are not equally serious:

  * **A fee, threshold or free-delivery flag that moved.** These are what a
    customer sees, and the re-split is supposed to move none of them. Anything
    here needs a reason written down beside it.
  * **A courier that changed, or a zone that was renamed.** These are internal.
    A zone changing hands is the point of the exercise; it is printed so the
    routing can be checked against the fare survey, not because it is wrong.

And one thing that is always a bug: **a point the new map does not claim at
all**. Since `088` there is no national default fee, so an unmatched pin is a
hard `UnserviceableAreaError` at checkout — a sliver left uncovered is an
address that can never be ordered to again.

Run from apps/api:

    python -m scripts.compare_delivery_maps
    python -m scripts.compare_delivery_maps --step 0.02   # a coarser sweep

The grid is the honest half of this. Named landmarks check the places somebody
thought of; the sweep checks the places nobody did, which is where a sliver
hides.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from app.services.delivery.delivery_zone_service import point_in_geometry

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic" / "versions"
DATA = ROOT / "app" / "data"

OLD_MIGRATION = "085_cost_banded_map.py"
NEW_MIGRATION = "126_cost_banded_map_v2.py"

#: The corners of the UAE, generously. Points outside every zone in *both* maps
#: are the sea and the empty quarter and are not interesting; a point that one
#: map claims and the other does not is the whole reason for the sweep.
BBOX = (22.5, 26.5, 51.0, 56.5)

#: Places with a fee somebody would notice, including every one the re-split
#: was drawn around.
LANDMARKS: list[tuple[str, float, float]] = [
    ("Melting Moments, Al Qasimia", 25.3304139, 55.3736131),
    ("Al Majaz Waterfront", 25.3213, 55.3820),
    ("Al Khan", 25.3306, 55.3600),
    ("Al Taawun (Khalid Lagoon)", 25.3160, 55.3720),
    ("Al Qasimia", 25.3450, 55.3900),
    ("Abu Shagara", 25.3330, 55.3900),
    ("Rolla", 25.3560, 55.3870),
    ("Al Nud", 25.3390, 55.4020),
    ("Sharjah Industrial Area", 25.3110, 55.4090),
    ("Maysaloon", 25.3220, 55.4250),
    ("Muwaileh Commercial", 25.3120, 55.4560),
    ("University City", 25.2960, 55.4900),
    ("Al Rahmaniya", 25.2650, 55.5170),
    ("Al Dhaid", 25.2880, 55.8810),
    ("Khor Fakkan", 25.3390, 56.3550),
    ("Kalba", 25.0680, 56.3500),
    ("Dibba Al Hisn", 25.6180, 56.2740),
    ("Ajman Corniche", 25.4052, 55.4384),
    ("Emirates City, Ajman", 25.4290, 55.5480),
    ("Masfout", 24.8180, 56.0490),
    ("Al Nahda, Dubai", 25.2980, 55.3760),
    ("Deira", 25.2530, 55.3320),
    ("Business Bay", 25.1850, 55.2680),
    ("Dubai Silicon Oasis", 25.1210, 55.3780),
    ("Al Barsha", 25.1120, 55.1980),
    ("Palm Jumeirah", 25.1120, 55.1390),
    ("Jebel Ali", 24.9880, 55.0870),
    ("Dubai Industrial City", 24.8720, 55.0300),
    ("Hatta", 24.8000, 56.1180),
    ("Umm al-Quwain City", 25.5650, 55.5530),
    ("Falaj Al Mualla", 25.3760, 55.8590),
    ("Al Jazirah Al Hamra", 25.7080, 55.7930),
    ("Ras al-Khaimah City", 25.7900, 55.9430),
    ("Al Rams", 25.8790, 56.0330),
    ("Abu Dhabi Corniche", 24.4750, 54.3520),
    ("Al Ain", 24.2070, 55.7450),
    ("Fujairah City", 25.1290, 56.3260),
]


def _zones(migration: str) -> list[dict]:
    """The fee table and the geometry a migration actually seeds, paired up."""
    source = (VERSIONS / migration).read_text()

    match = re.search(r'"(uae_delivery_zones[a-z0-9.]*\.json)"', source)
    if match is None:
        raise SystemExit(f"{migration} names no geometry file")
    shapes = {
        z["name"]: z["geometry"]
        for z in json.loads((DATA / match.group(1)).read_text())
    }

    # From the opening bracket of the literal, not from the annotation — which
    # has brackets of its own and would close the slice three characters in.
    start = source.index("= [", source.index("ZONES: list[")) + 2
    end = source.index("\n]", start) + 2
    table = ast.literal_eval(
        source[start:end]
        .replace("OUTER_FEE", '"80.00"')
        .replace("OUTER_THRESHOLD", '"200.00"')
    )
    return [
        {
            "name": name,
            "fee": fee,
            "threshold": threshold,
            "provider": provider,
            "free": free,
            "geometry": shapes[name],
        }
        for name, fee, threshold, provider, free in table
    ]


def _resolve(zones: list[dict], lat: float, lng: float) -> dict | None:
    """`find_zone`, in miniature: the first match in display order."""
    for zone in zones:
        if point_in_geometry(lat, lng, zone["geometry"]):
            return zone
    return None


def _price(zone: dict | None) -> tuple[str, str, bool] | None:
    return None if zone is None else (zone["fee"], zone["threshold"], zone["free"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step",
        type=float,
        default=0.01,
        help="grid spacing in degrees (0.01 ≈ 1.1 km). Default 0.01.",
    )
    args = parser.parse_args()

    old = _zones(OLD_MIGRATION)
    new = _zones(NEW_MIGRATION)
    print(f"{OLD_MIGRATION}: {len(old)} zones")
    print(f"{NEW_MIGRATION}: {len(new)} zones\n")

    price_moves: dict[tuple, list[tuple[float, float]]] = {}
    provider_moves: dict[tuple[str, str, str, str], int] = {}
    lost: list[tuple[float, float]] = []
    gained = 0

    def compare(lat: float, lng: float, label: str | None = None) -> None:
        nonlocal gained
        was, now = _resolve(old, lat, lng), _resolve(new, lat, lng)
        if was is None and now is None:
            return
        if now is None:
            lost.append((lat, lng))
            if label:
                print(f"  UNSERVICEABLE  {label}: {was['name']} → nothing")
            return
        if was is None:
            gained += 1
            return
        if _price(was) != _price(now):
            key = (was["name"], now["name"], *_price(was), *_price(now))
            price_moves.setdefault(key, []).append((lat, lng))
            if label:
                print(
                    f"  FEE MOVED      {label}: {was['name']} "
                    f"{was['fee']}/{was['threshold']} → {now['name']} "
                    f"{now['fee']}/{now['threshold']}"
                )
        elif was["provider"] != now["provider"]:
            key = (was["name"], was["provider"], now["name"], now["provider"])
            provider_moves[key] = provider_moves.get(key, 0) + 1

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

    if price_moves:
        print("Fees that moved")
        for (was, now, f0, t0, e0, f1, t1, e1), points in sorted(
            price_moves.items(), key=lambda kv: -len(kv[1])
        ):
            lat, lng = points[0]
            print(
                f"  {len(points):>6} pts  {was} {f0}/{t0}/{e0} → "
                f"{now} {f1}/{t1}/{e1}   e.g. {lat:.3f}, {lng:.3f}"
            )
        print()
    else:
        print("Fees that moved: none\n")

    if provider_moves:
        print("Couriers that changed (fee unchanged)")
        for (was, p0, now, p1), count in sorted(
            provider_moves.items(), key=lambda kv: -kv[1]
        ):
            print(f"  {count:>6} pts  {was} [{p0}] → {now} [{p1}]")
        print()

    print(f"Points the new map does not claim and the old one did: {len(lost)}")
    for lat, lng in lost[:20]:
        print(f"  {lat:.4f}, {lng:.4f}")
    if len(lost) > 20:
        print(f"  ... and {len(lost) - 20} more")
    print(f"Points the new map claims and the old one did not: {gained}")

    return 1 if lost else 0


if __name__ == "__main__":
    raise SystemExit(main())
