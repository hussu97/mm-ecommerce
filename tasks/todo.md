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

## Phase 2 — Per-area Voronoi polygon map + Sharjah-branch provider simulation + cart backfill ✅ DONE
- [x] Extracted 97 areas -> app/data/uae_delivery_areas.json
- [x] Probed PROD Slider fares for all 97 areas from the VM (34.18.98.2); merged with real lalamove/noon -> app/data/courier_costs.json
- [x] scripts/build_delivery_areas.py: per-emirate Voronoi (gap-filled), fee inherited from v2 band by centroid, provider = cheapest + >5 lalamove margin + fee>=80->3rd party + bike/car tier, N/S batch groups, per-polygon alternates (noon added for Sharjah slider)
- [x] Committed v5 geojson + assignments (97 polys: 30 slider_bike, 22 slider_car, 8 noon, 1 lalamove, 36 third_party)
- [x] Migration 174: rename Dubai->South of Sharjah / Northern Emirates->North of Sharjah, seed new ACTIVE version, cart delivery_quote_* backfill; verified on throwaway PG (upgrade/downgrade/re-upgrade)
- [x] test_per_area_map.py validates committed map vs rules (103 cases); full suite 2711 green; ruff clean
- [x] NOTE: prod-fare result — Slider dominates, lalamove=1 (batching minimal). Filename collision bug fixed (v3 was 085's file; now v5).
- [ ] PENDING: push to main + deploy to prod + verify (per user request)
## Phase 3 — Admin polygon data-table UI ✅ DONE
- [x] Backend: GET /delivery-zones/polygons (paginated/search/sort/filter) + PolygonPage schema; relaxed update_polygon guard (attrs editable in place on active version) + invalidate_cache; regenerated @mm/types (no drift)
- [x] Frontend: FulfilmentProvider += slider_bike/slider_car; provider-labels + ZoneMap colors; deliveryZonesApi.listPolygons; new PolygonTable.tsx (version switcher + search + provider/branch/batch filters + click-sort + Pagination 50-2000 + in-place ZoneEditForm); page.tsx uses it; deleted dead VersionCard/ZoneRow
- [x] admin type-check + lint clean (0 errors); backend 2711 tests green

## Phase 4 — Courier switching bike->car (not car->bike) ✅ DONE
- [x] Backend delivered in Phase 1 (reassignment quote/move recognise slider tiers; allowed_targets directional guard car-never->bike)
- [x] Frontend: DeliveryPanel + courier-labels render slider_bike/car labels + amber badge + status control; dialog offers slider_car for a slider_bike order (targets come from server allowed_targets)

## Integration audit (whole order journey + reports + admin) — DONE
Three parallel audits. Real gaps found + FIXED:
- [x] courier_catalog.COURIER_NAMES missed the tiers -> dashboard per-courier scorecard SILENTLY DROPPED slider_bike/car orders + revenue, no badge, no filter option. Added both + logo falls back to slider.png. (order_query ALL_COURIER_CODES/grouping fixed via this.)
- [x] slider_service._delivery_for matched webhooks on provider=="slider" only -> tier orders' status webhooks lost. Now .in_(PROVIDERS) (3 filters).
- [x] courier_service.cancel routed slider_bike/car cancellation to Lalamove. Now in SLIDER_PROVIDERS.
- [x] admin couriers.ts COURIER_OPTIONS + courierLogo + delivery courier-labels.ts missed tiers. Added.
- [x] test_order_query updated + new tier test. Full suite 2723 green; ruff + admin type-check clean.
- Confirmed SAFE (provider-agnostic): order_economics margin, daily_sales_email (by source), analytics commerce (by method), aggregator reconcile/fees, export_data, delivery_promise (keys on zone provider -> finds 60min tier rows), storefront apps/web (no courier field). NOTE: order-journey audit agent's premise was WRONG (claimed Phase 1 code missing) — verified Phase 1 intact; only #11 webhook + #3 cancel were real.

## Merge with main — DONE
- [x] Merged origin/main (8 commits ahead); re-chained migrations onto main's 173_branch_weekly (mine now 174_slider_vehicle_couriers, 175_per_area_courier_map); single alembic head verified on throwaway; 2723 tests + type-check green.

## DEPLOY (user: reverify all -> fix bugs+optim -> push to main direct -> deploy green -> verify prod)

## Notes
- New versioning: attributes (fee/threshold/default courier) editable in place on active version; new version only for geometry changes.
- Cutover backfill: re-resolve cart delivery_quote_* against new active version; users/addresses derive zone live.

---

# Inventory v2 Production Audit (2026-09-05)

## Invariants and business-logic review

- [x] Compare the implementation with the approved inventory plan and both repositories' `CLAUDE.md` rules.
- [x] Audit recipe publication, recursive expansion, snapshot history, and Foodics import idempotency.
- [x] Audit ledger sequencing, valuation, reversals/returns, immutable SQL guards, and projection rebuild parity.
- [x] Audit count/report concurrency, business-day aggregation, approval thresholds, and opening-count rollout safety.
- [x] Audit branch scoping, permissions, generated contracts, admin maintainability, and shared iPad/iPhone behavior.

## Fixes and regression proof

- [x] Fix every correctness or scalability defect found, keeping compatibility adapters intact.
- [x] Add focused regression tests for the costing, recursive yield, report reconciliation, Foodics import, source-event atomicity, and POS validation failures found.
- [x] Re-run migration upgrade/downgrade/re-upgrade on a fresh PostgreSQL database (`185_inventory_v2` head).
- [x] Run backend lint/tests, regenerate and verify OpenAPI contracts, and run admin type/lint checks.
- [x] Run `swift test` and build both the iPad and iPhone schemes.
- [x] Record the final findings and verification evidence here, then commit coherent audit fixes with the required author.

## Audit findings closed

- [x] Ledger quantity and valuation now use the correct storage/ingredient conversion snapshots; transfer receipts preserve the source branch's moving-average value and production locks the whole value flow.
- [x] Projection rebuild mirrors live posting, including exact receipt reversals and value-only cost adjustments, and streams long histories with bounded memory.
- [x] Order consumption is acceptance-sequenced and savepoint-atomic; expected domain failures become durable no-movement exceptions instead of partially committed stock.
- [x] Published recipes/source snapshots/closed ledger rows are SQL-immutable; draft preview validates recursive expansion, phantom dependencies, yield and cycles before activation.
- [x] Counts and shift reports lock state transitions, detect any item movement since prefill, reprice variances at current average cost, require reasons, and post/approve atomically with retry-safe saves.
- [x] Every active branch is database-enforced to have exactly one active default stock container; future branch creation provisions its container and rollout settings in the same transaction.
- [x] Foodics staging is sanitized, UUID/SKU-based, unit-aware and rerunnable; zero values, duplicate-looking SKUs, conflicts and ambiguous mappings remain visible for review.
- [x] Branch permissions are enforced across legacy and v2 APIs; ledger list labels are bulk-loaded and rebuild history is streamed rather than growing memory without bound.
- [x] Admin recipe selection/validation, stock-audit feedback and integrity empty states are usable without raw UUIDs or false “no drift” claims.
- [x] Shared POS flow requires variance/waste/internal-use reasons, uses stable per-payload retry keys, refreshes stale expected stock, supports explained skips, survives termination in Keychain, and adapts on both phone and tablet.

## Verification

- Backend: `2810 passed, 189 skipped`; Ruff clean.
- Inventory-focused backend: `52 passed` after the final service changes.
- Contracts/admin: generated OpenAPI fresh; types `3 passed`; admin `62 passed`; TypeScript clean; ESLint has two pre-existing warnings outside inventory and no errors.
- Database: fresh PostgreSQL upgrade to head, downgrade to 184, and re-upgrade to `185_inventory_v2` passed.
- POS: `332 passed`; `MMPos` iPad simulator build passed; `MMPosPhone` iPhone simulator build passed.
