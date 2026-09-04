"""Probe live Slider fares for every delivery area, from the whitelisted VM.

Slider's production fare API only answers from the VM's whitelisted egress IP
(34.18.98.2), never from a laptop or sandbox. So this runs INSIDE the live api
container on the VM:

    # find the live slot (api-1 or api-green-1) from `docker ps`, then:
    docker exec -w /app <live-api-slot> sh -lc \
        'PYTHONPATH=/app python -u scripts/probe_courier_fares.py'
    # copy the result back:
    docker cp <live-api-slot>:/tmp/courier_costs.json /tmp/courier_costs.json
    gcloud compute scp mm-backend:/tmp/courier_costs.json ./courier_costs.json \
        --zone me-central1-a

It is deliberately **minimal** — it imports only the Slider provider (no DB, no
other services), because a full-app probe running beside the live process OOMs
the 1 GB e2-small. Fare calls are read-only (no bookings).

**By default only areas missing a Slider fare are probed** — an area already in
`courier_costs.json` keeps its committed fare, which preserves a hand-tuned or
averaged survey while filling in newly-added areas. Set `PROBE_ALL=1` to re-probe
every area from scratch.

Lalamove and noon Send were never IP-blocked and come from the rate card, not a
live call. Every area already priced keeps its committed lalamove / noon; an
area new since the last probe is modelled from the Slider road distance:
  * lalamove  ~= round(18 + 0.68 * road_km), refused (ERR_OUT_OF_SERVICE) past
    175 km — the range the survey showed Lalamove serving.
  * noon Send  = 12 flat inside the kitchen's emirate (Sharjah) within its 20 km
    road ceiling, else it cannot serve.

Writes app/data/courier_costs.json in place AND /tmp/courier_costs.json, and
prints a summary. Commit the file; `scripts/build_delivery_areas.py` reads it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

# Slider only — importing DB/session or the other courier services is what OOMs
# the slot. `provider` is the module-level client, configured from settings.
from app.services.providers.slider_provider import SliderError, aed, provider


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
COSTS = DATA / "courier_costs.json"

#: The bike's road ceiling — the same `settings.SLIDER_BIKE_MAX_KM`. Above it the
#: survey only ever reached an area on a car, so the recorded tier is `car`.
BIKE_MAX_KM = 35.0
#: Lalamove's serving range and its fitted rate line (from the committed survey).
LALAMOVE_MAX_KM = 175.0
#: noon Send: flat inside Sharjah within this road ceiling, nothing beyond.
NOON_FLAT = 12
NOON_MAX_KM = 20.0
#: Straight-line -> road, when Slider gave no distance (its own is preferred).
DETOUR = 1.44


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


def _noon_model(emirate: str, road_km: float | None) -> int | None:
    if emirate != "Sharjah" or road_km is None or road_km > NOON_MAX_KM:
        return None
    return NOON_FLAT


async def main() -> None:
    doc = json.loads(AREAS.read_text())
    pickup = doc["pickup"]
    areas = doc["areas"]
    klat, klng = float(pickup["lat"]), float(pickup["lng"])
    kaddr = pickup.get("address", "")

    existing = json.loads(COSTS.read_text())
    prior = existing.get("costs", {})
    probe_all = os.environ.get("PROBE_ALL") == "1"

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
        noon = was["noon_send"] if "noon_send" in was else _noon_model(emirate, road_km)

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
        "source": (
            "slider: PROD probe from VM 34.18.98.2 via scripts.probe_courier_fares "
            "(already-surveyed areas kept, new areas probed); lalamove + noon_send: "
            "rate card (committed kept, new modelled)"
            if not probe_all
            else existing.get("source", "")
        ),
        "note": existing.get("note", ""),
        "costs": costs,
    }
    COSTS.write_text(json.dumps(out, indent=2) + "\n")
    Path("/tmp/courier_costs.json").write_text(json.dumps(out, indent=2) + "\n")
    print(
        f"\nprobed {fresh} new area(s), kept {kept} already-surveyed; total "
        f"{len(costs)}. wrote {COSTS} and /tmp/courier_costs.json"
    )


if __name__ == "__main__":
    asyncio.run(main())
