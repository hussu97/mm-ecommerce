# Slider Prod Integration + Per-Area Polygon Rebuild

Plan: /Users/hussainabbasi/.claude/plans/lets-plan-for-slider-crispy-wirth.md

## Phase 0 — Slider production cutover (remove pilot gate + pilot free delivery) ✅ DONE
- [x] courier_service.py: effective_provider drops the gate (slider→slider for all when configured; fallback only when not configured); drop user_id/email; module + carrier_for + estimate_for_point docstrings
- [x] delivery_service.py: drop user_id/email from price/calculate_fee/quote/quote_priced; remove trial waiver
- [x] order_pricing.py: drop user_id/email + trial free-delivery zeroing from compute_order_totals
- [x] order_service.py: drop user_id/email at 3 call sites (compute_order_totals x2, quote_priced, effective_provider)
- [x] delivery.py (api): drop user_id/email from /calculate + /quote
- [x] Delete trial_customer.py; remove SLIDER_TRIAL_EMAILS from all 5 W9 locations (.env.example, PRODUCTION.md, deploy.yml, rollback.yml, docker-compose.prod.yml)
- [x] Tests: deleted test_trial_customer_free_delivery.py + test_slider_rollout_is_a_no_op.py; rewrote gate section of test_courier_routing.py; fixed test_delivery_quote_privacy.py, test_delivery_fee_agreement.py, test_service_layout.py, test_compose_env_allowlist.py
- [x] Full unit suite green (2592 passed), ruff format+check clean

## Phase 1 — slider_bike + slider_car distinct fulfilment types ✅ DONE
- [x] FulfilmentProviderEnum: SLIDER_BIKE, SLIDER_CAR (SLIDER kept legacy); DEFAULT_ALTERNATES entries
- [x] Migration 173: couriers slider_bike/slider_car (60min) + alternates seed; verified on throwaway PG (upgrade/downgrade/re-upgrade)
- [x] slider_service: vehicle_for_provider/provider_for_vehicle; estimate pins tier; dispatch books pinned tier, falls bike→car on Slider substitution, records ACTUAL tier as provider (+requested_vehicle in breakdown), no original_provider on auto-substitution
- [x] courier_service: SLIDER_PROVIDERS in books_itself/is_enabled/effective_provider/_dispatch_once/FALLBACKS/estimate_for_point
- [x] order_delivery: _status_family collapses slider_bike/car→slider for status maps
- [x] orders API refresh map + fulfilment_service _BOOKED_BY_US learn the tiers
- [x] fulfilment_reassignment: quote/move recognise SLIDER_PROVIDERS; allowed_targets directional guard (car never→bike) — **Phase 4 backend delivered early here**
- [x] +16 tests (helpers, effective_provider tiers, estimate vehicle-pass, dispatch records-car substitution, allowed_targets bike↔car); full suite 2608 green; ruff clean

## Phase 2 — Per-area Voronoi polygon map + Sharjah-branch provider simulation + cart backfill
## Phase 3 — Admin polygon data-table UI (paginated/search/sort/filter + version switch + in-place attr edit)
## Phase 4 — Courier switching bike->car (not car->bike) — backend DONE in Phase 1; remaining: admin ChangeFulfilmentDialog/DeliveryPanel UI (do with Phase 3)

## Notes
- New versioning: attributes (fee/threshold/default courier) editable in place on active version; new version only for geometry changes.
- Cutover backfill: re-resolve cart delivery_quote_* against new active version; users/addresses derive zone live.
