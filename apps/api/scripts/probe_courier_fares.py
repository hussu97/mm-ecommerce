"""Probe live Slider fares for every delivery area, from the whitelisted VM.

Slider's production fare API only answers from the VM's whitelisted egress IP
(34.18.98.2), never from a laptop or sandbox. So this runs INSIDE the live api
container on the VM:

    # find the live slot (api-1 or api-green-1) from `docker ps`, then:
    docker exec -w /app <live-api-slot> sh -lc \
        'PYTHONPATH=/app python -u scripts/probe_courier_fares.py --branch K001'
    # copy the result back (note the per-branch filename):
    docker cp <live-api-slot>:/tmp/courier_costs.K001.json /tmp/courier_costs.K001.json
    gcloud compute scp mm-backend:/tmp/courier_costs.K001.json \
        ./courier_costs.K001.json --zone me-central1-a

**Fares are origin-specific, so each branch is a separate VM run.** A run's
pickup origin is the branch row's own pin (`--branch K001` = Sharjah kitchen,
`--branch B001` = Barsha), loaded from the DB so the probe and the delivery
geometry cannot drift from the branch. Slider prices every delivery area *from
that origin*, and because the API is IP-whitelisted to the VM there is no way to
probe a second origin from the first: register each branch and run this once per
branch on the VM.

It is deliberately **minimal** — it imports only the Slider provider and one
`Branch` lookup (no other services), because a full-app probe running beside the
live process OOMs the 1 GB e2-small. Fare calls are read-only (no bookings).

**By default only areas missing a Slider fare are probed** — an area already in
this branch's `courier_costs.<REF>.json` keeps its committed fare, which
preserves a hand-tuned or averaged survey while filling in newly-added areas.
Set `PROBE_ALL=1` to re-probe every area from scratch.

Lalamove and noon Send were never IP-blocked and come from the rate card, not a
live call. Every area already priced keeps its committed lalamove / noon; an
area new since the last probe is modelled from the Slider road distance:
  * lalamove  ~= round(18 + 0.68 * road_km), refused (ERR_OUT_OF_SERVICE) past
    175 km — the range the survey showed Lalamove serving.
  * noon Send  = 12 flat inside the **branch's own emirate** (Sharjah for K001,
    Dubai for B001) within its 20 km road ceiling, else it cannot serve.

Writes app/data/courier_costs.<REF>.json in place AND /tmp/courier_costs.<REF>.json,
and prints a summary. Commit the file; `scripts/build_delivery_areas.py` reads
every branch's copy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import select

# Slider + one Branch lookup only — importing DB/session or the other courier
# services is what OOMs the slot. `provider` is the module-level Slider client,
# configured from settings.
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.services.providers.slider_provider import SliderError, aed, provider

#: The branches that fulfil, and the default. Each is probed from its own pin on
#: its own VM run; there is no single-run way to price a second origin because
#: the fare API is IP-whitelisted (see the module docstring).
BRANCH_CHOICES = ("K001", "B001")
DEFAULT_BRANCH = "K001"


def _find_data() -> Path:
    """The `app/data` directory, wherever this script is run from.

    Locally it is `apps/api/app/data`; inside the deployed image the `app`
    package sits at `/app/app`, so the script copied in may be run from `/app`,
    `/app/scripts`, or `/tmp`. Walk up looking for the areas file rather than
    assuming a fixed depth.
    """
    here = Path(__file__).resolve()
    for base in [here.parent, *here.parents]:
        for candidate in (base / "app" / "data", base / "data", base):
            if (candidate / "uae_delivery_areas.json").exists():
                return candidate
    raise FileNotFoundError("could not locate app/data/uae_delivery_areas.json")


DATA = _find_data()
AREAS = DATA / "uae_delivery_areas.json"

#: The bike's road ceiling — the same `settings.SLIDER_BIKE_MAX_KM`. Above it the
#: survey only ever reached an area on a car, so the recorded tier is `car`.
BIKE_MAX_KM = 35.0
#: Lalamove's serving range and its fitted rate line (from the committed survey).
LALAMOVE_MAX_KM = 175.0
#: noon Send: flat inside the branch's emirate within this road ceiling, nothing
#: beyond.
NOON_FLAT = 12
NOON_MAX_KM = 20.0
#: Straight-line -> road, when Slider gave no distance (its own is preferred).
DETOUR = 1.44


def _costs_path(reference: str) -> Path:
    """This branch's committed cost file, e.g. courier_costs.K001.json."""
    return DATA / f"courier_costs.{reference}.json"


def _stop(lat: float, lng: float, address: str) -> dict:
    """One end of a run, in the shape Slider's fare endpoint wants."""
    return {
        "latitude": round(float(lat), 7),
        "longitude": round(float(lng), 7),
        "address": address[:250],
    }


def _fare(payload: dict, vehicle: str) -> tuple[float | None, float | None]:
    """(price, road_km) for one vehicle out of a fare response, or (None, ...).

    Same shape the app parses: `vehicles` is a list of `{vehicle_type,
    is_available, delivery_fee}`; `distance_km` is top-level and belongs to the
    run, not the tier.
    """
    tiers = payload.get("vehicles")
    block = None
    if isinstance(tiers, list):
        block = next(
            (
                t
                for t in tiers
                if isinstance(t, dict)
                and str(t.get("vehicle_type") or "").strip().lower() == vehicle
            ),
            None,
        )
    dist = payload.get("distance_km")
    try:
        road_km = float(dist) if dist is not None else None
    except (TypeError, ValueError):
        road_km = None
    if not isinstance(block, dict) or block.get("is_available") is False:
        return None, road_km
    cost = aed(block.get("delivery_fee"))
    return (float(cost) if cost is not None else None), road_km


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    )
    return 6371.0 * 2 * asin(sqrt(a))


def _lalamove_model(road_km: float | None) -> tuple[int | None, str | None]:
    if road_km is None:
        return None, None
    if road_km > LALAMOVE_MAX_KM:
        return None, "ERR_OUT_OF_SERVICE"
    return round(18 + 0.68 * road_km), None


def _noon_model(
    area_emirate: str, origin_emirate: str, road_km: float | None
) -> int | None:
    """Flat noon-Send fare, or None where it cannot serve.

    noon Send cannot cross an emirate boundary, so it serves an area only when
    the area sits in the branch's *own* emirate (Sharjah for K001, Dubai for
    B001) and inside the 20 km road ceiling.
    """
    if area_emirate != origin_emirate or road_km is None or road_km > NOON_MAX_KM:
        return None
    return NOON_FLAT


async def _branch_origin(reference: str) -> tuple[float, float, str, str]:
    """This branch's pickup pin and emirate, from its DB row.

    Returns (lat, lng, emirate, address). The emirate is the branch's `city`
    column ("Sharjah", "Dubai") — the same vocabulary the areas file uses for
    `emirate` — which is what noon Send's same-emirate rule is checked against.
    """
    async with AsyncSessionFactory() as db:
        branch = (
            await db.execute(select(Branch).where(Branch.reference == reference))
        ).scalar_one_or_none()
    if branch is None:
        raise SystemExit(f"no branch with reference {reference!r}")
    if branch.latitude is None or branch.longitude is None:
        raise SystemExit(f"branch {reference} has no coordinates")
    emirate = (branch.city or "").strip()
    if not emirate:
        raise SystemExit(
            f"branch {reference} has no city — needed for the noon-Send emirate gate"
        )
    address = branch.address or branch.name
    return float(branch.latitude), float(branch.longitude), emirate, address


async def main(reference: str) -> None:
    doc = json.loads(AREAS.read_text())
    areas = doc["areas"]

    klat, klng, origin_emirate, kaddr = await _branch_origin(reference)

    costs_file = _costs_path(reference)
    try:
        existing = json.loads(costs_file.read_text())
    except FileNotFoundError:
        existing = {}
    prior = existing.get("costs", {})
    probe_all = os.environ.get("PROBE_ALL") == "1"

    print(f"branch  : {reference} ({origin_emirate})")
    print(f"pickup  : {klat}, {klng}")

    costs: dict[str, dict] = {}
    fresh = kept = 0
    for area in areas:
        label = area["label"]
        lat, lng = float(area["lat"]), float(area["lng"])
        emirate = area["emirate"]

        # Keep an already-surveyed area's committed fare (preserves a hand-tuned
        # or averaged Slider survey); only fill in the new ones. `PROBE_ALL=1`
        # forces a full re-probe.
        was_slider = prior.get(label, {})
        if not probe_all and (
            was_slider.get("slider_bike") is not None
            or was_slider.get("slider_car") is not None
        ):
            costs[label] = was_slider
            kept += 1
            continue

        bike = car = road_km = None
        minutes = None
        try:
            payload = await provider.fare(
                pickup=_stop(klat, klng, kaddr),
                delivery=_stop(lat, lng, label),
            )
            bike, road_km = _fare(payload, "bike")
            car, road_km2 = _fare(payload, "car")
            road_km = road_km if road_km is not None else road_km2
            minutes = payload.get("duration_minutes")
            fresh += 1
        except SliderError as exc:
            print(f"  ! {label}: Slider error ({exc})")
        except Exception as exc:  # noqa: BLE001 — one bad area must not stop the run
            print(f"  ! {label}: {type(exc).__name__} {exc}")

        if road_km is None:
            road_km = round(_haversine_km(klat, klng, lat, lng) * DETOUR, 2)

        tier = "bike" if bike is not None and road_km <= BIKE_MAX_KM else "car"

        # Lalamove / noon: keep the committed rate-card value where we already had
        # one; model it only for an area new since the last probe.
        was = prior.get(label, {})
        if "lalamove" in was:
            lalamove, lala_err = was.get("lalamove"), was.get("lalamove_error")
        else:
            lalamove, lala_err = _lalamove_model(road_km)
        noon = (
            was["noon_send"]
            if "noon_send" in was
            else _noon_model(emirate, origin_emirate, road_km)
        )

        costs[label] = {
            "km": road_km,
            "minutes": minutes,
            "slider_bike": bike,
            "slider_car": car,
            "slider_tier": tier,
            "slider_distance_km": road_km,
            "lalamove": lalamove,
            "lalamove_error": lala_err,
            "noon_send": noon,
        }
        mark = "bike" if bike is not None else "car-only" if car is not None else "NONE"
        print(f"  {label:32} {road_km:6.1f}km  bike={bike} car={car} [{mark}]")

    out = {
        # The origin this run priced from — read back by build_delivery_areas so
        # its per-branch bike-reachability and emirate rules use the same pin.
        "branch": {
            "ref": reference,
            "emirate": origin_emirate,
            "lat": klat,
            "lng": klng,
            "address": kaddr,
        },
        "source": (
            f"slider: PROD probe from VM 34.18.98.2 via scripts.probe_courier_fares "
            f"--branch {reference} (already-surveyed areas kept, new areas probed); "
            "lalamove + noon_send: rate card (committed kept, new modelled)"
            if not probe_all
            else existing.get("source", "")
        ),
        "note": existing.get(
            "note",
            "Slider fares are live production, priced from this branch's pin (bike "
            "offered cross-emirate up to its road ceiling; null bike = car-only). "
            "lalamove_error non-null = Lalamove refuses. noon_send null = cannot "
            "serve (crosses the branch's emirate / >20km).",
        ),
        "costs": costs,
    }
    payload = json.dumps(out, indent=2) + "\n"
    # /tmp is the one the caller retrieves and must always be written. The
    # in-place copy is a convenience for a local run and is best-effort: inside
    # the deployed image the data dir is owned by the app user, so a container
    # exec cannot write it, and that must not fail the probe.
    tmp = Path(f"/tmp/courier_costs.{reference}.json")
    tmp.write_text(payload)
    try:
        costs_file.write_text(payload)
        where = f"{costs_file} and {tmp}"
    except OSError:
        where = f"{tmp} (in-place copy not writable here)"
    print(
        f"\nprobed {fresh} new area(s), kept {kept} already-surveyed; total "
        f"{len(costs)}. wrote {where}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--branch",
        choices=BRANCH_CHOICES,
        default=DEFAULT_BRANCH,
        help="branch reference whose pin is the pickup origin (default K001)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.branch))
