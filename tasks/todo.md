# Slider integration — done

Plan: `tasks/slider-integration.md`. Branch `claude/slider-integration-4b4113`,
worktree `.claude/worktrees/sms-verification-branding-bab18e`, off `main@4ee22ad`.

## Phase A — map re-split ("Cost-banded map v2")
- [x] New `BANDS` (22 zones from 15) + `REMAINDER_NAMES` in `scripts/build_delivery_zones.py`
- [x] Regenerated `uae_delivery_zones.geojson.json`, froze `.v4.`
- [x] `126_cost_banded_map_v2` — one transaction, fees carried through, batch groups restated
- [x] Fixed `create_version`: copies `batch_group_id`; the dead window loop is gone
      (`_windows_of` has taken a *group* id since 088 and was returning `[]`)
- [x] `LIVE_MIGRATION` + expectations in `tests/unit/test_delivery_zone_map.py`
- [x] `scripts/compare_delivery_maps.py` — 37 landmarks + a 98k-point grid sweep

## Phase B — Slider as a third courier
- [x] `providers/slider_provider.py` — User-Agent, non-JSON is an error, 4xx is not retried
- [x] `slider_service.py` — one pure `vehicle_for`, called by estimate and dispatch
- [x] `SLIDER` provider, `SliderStatusEnum`, status sets, rank
- [x] `courier_service.carrier_for`, `driver_assignment.Driver.from_slider`
- [x] `fulfilment_service._BOOKED_BY_US`; `127_slider_courier`
- [x] Admin TS + `packages/types` regenerated from OpenAPI

## Phase C — gated rollout
- [x] `trial_customer.py` recovered from `c47aab7^`, on `SLIDER_TRIAL_EMAILS`
- [x] Swap in `_dispatch_once`, and **in `batching_service.reserve`** — see below
- [x] Free-delivery waiver in `calculate_fee`, `quote_priced`, `compute_order_totals`
- [x] `test_trial_customer_free_delivery.py` recovered; the agreement test kept

## Phase D — webhooks + config
- [x] `POST /webhooks/slider` (token enforced) and `/webhooks/slider/staging` (inert)
- [x] Eleven `SLIDER_*` settings in all five locations

## Three departures from the plan, each to protect its own "no-op" guarantee

1. **`batching_service.reserve` asks `carrier_for` too.** The plan put the swap
   only in `_dispatch_once`, but `reserve` runs at confirmation and keys off the
   literal string `lalamove`. Left alone, every Dubai and Ajman order would have
   skipped its batch window the day the map named Slider and gone out alone at
   roughly three times the courier cost.
2. **The Slider zones keep their `batch_group_id`.** A grouped zone is promised
   its group's next window close; an ungrouped one is promised its courier's own
   answer. Detaching them would have changed what every customer in those zones
   is told while their orders carried on riding the run.
3. **`couriers.unbatched_promise_minutes` is 90, not the plan's 60.** The promise
   is read off the zone's courier while the gate hands all but one account back
   to noon Send, who promise 90. Sixty would have promised `Sharjah Core` an hour
   and delivered in an hour and a half. One admin edit when the gate comes off.

Pinned by `tests/unit/test_slider_rollout_is_a_no_op.py`.

## Rebased onto `main`, and what the QA pass then found

`main` gained zone alternates, courier reassignment and its own `create_version`
fix while this branch was in flight. Rebasing dropped my copy of that fix — theirs
landed first and goes further — renumbered the migrations to `126`/`127`/`128`,
and turned up six things the tests did not:

1. **The gate was writing `original_provider`.** That column means "a human
   moved this order", and three things read it that way — the admin's "moved
   from X" badge, `allowed_targets`, and `fulfilment_service._estimate`, which
   reads it as "written against a third-party zone" and answers tomorrow rather
   than an hour. It would have put a hand-moved badge and a next-day promise on
   nearly every Dubai order. Removed; the noon Send fallback never set it either.
2. **`_speed_of` asked the courier's name, not the schedule.** `Sharjah Core`
   would have dropped from "express" to "same day" the moment it was handed to
   Slider, for no reason a customer could see. It asks `is_batched` now.
3. **The checkout quoted the wrong vehicle.** A quote has a pin and no order, so
   it never learned the drop's emirate and priced the car everywhere — including
   inside Sharjah, where the booking uses the bike. The zone name is threaded
   through and `emirate_of` reads an emirate out of either shape.
4. **The fare cache was keyed on the pin alone**, so two quotes at one point in
   two zones would have shared one tier's price.
5. **The COD ceiling measured `order.total`**, not the cash the rider is actually
   handed — which is what goes into `cod_amount`.
6. **`fulfilment_reassignment` did not know Slider.** An admin would have been
   offered it as a target and got "Unknown courier 'slider'".

Two additions the audit argued for: `slider_service.refresh`, because Slider's
statuses only arrive by push and a lost one had no recovery path during a pilot
on production; and `courier_service.may_be_carried_by`, which states once that a
Slider zone's orders can ride a Lalamove run — otherwise
`assert_group_fits_polygon` and `update_polygon` would have detached six zones
from the runs that keep their promise honest.

## One fee moves, deliberately

Ras al-Khaimah beyond 78 km — Al Rams and the northern tip — was leftover at
80.00/200.00 third-party and is now `Ras al-Khaimah North` at 50.00/100.00 on
Lalamove. That is what the plan's routing table asks for (all eight measured RAK
areas go by Lalamove car) and it is a fee going **down**. Everything else on the
map is identical: verified over 98k grid points, nothing lost coverage.
