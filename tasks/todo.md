# Aggregator: manual run trigger + placed-at timestamp fix

## Part 1 — Manual "Run now" trigger from the admin Runs page

Backend (`apps/api`):
- [ ] Add a tracked-background fire-and-forget entrypoint in `ingest.py` that runs
      `run_daily_once()` (sales→finance→promote→reconcile, all channels) off a
      module-level task set (convention #5: no BackgroundTasks). Overlap is a
      no-op via the existing advisory locks.
- [ ] Add `POST /aggregators/runs/trigger` in `api/v1/aggregators.py`, gated by
      `require("reports.sales")` (same gate as the Runs page / dashboard). Refuse
      with ServiceUnavailableError when ingest is disabled (`is_enabled()`).
- [ ] Add `AggregatorRunTriggerOut` response schema in `schemas/aggregator.py`.
- [ ] Regenerate the OpenAPI contract + `@mm/types` (convention #8).

Frontend (`apps/admin`):
- [ ] Add `aggregatorRunsApi.trigger()` binding in `lib/api.ts`.
- [ ] Add a "Run now" button on `aggregators/runs/page.tsx` (loading state, toast,
      refetch on success).

## Part 2 — Fix aggregator order created_at timestamps (all aggregators)

Root cause: promotion already sets `Order.created_at = agg.placed_at`, but three
providers (Talabat CSV, Keeta page JSON, Noon OMS/RMS) produce a **naive** Dubai
wall-clock `placed_at`. Written to a `timestamptz` column it is read back as UTC,
shifting a 23:16 Dubai order to 03:16 next morning (+4h) — so an order looks
"placed" hours after the sync ran. Careem/Deliveroo already emit tz-aware values.

Fix at the single ingest chokepoint every sales order funnels through
(`upsert_order`, covering both the httpx sweep and the Keeta push):
- [ ] Add `_aware_business()` helper (reuse `_DUBAI`): stamp naive marketplace
      timestamps with Dubai; leave already-aware values untouched.
- [ ] Normalize `placed_at`/`accepted_at`/`delivered_at`/`cancelled_at` in
      `upsert_order` through it, documented at the line.
- [ ] Unit test: a naive placed_at is persisted as the correct tz-aware instant.

Existing rows self-correct: a re-scrape re-upserts the now-correct placed_at,
which advances `updated_at`, which drives `promote._refresh_order` to reset
`created_at` (it already does this).

## Verification
- [x] Run aggregator ingestion/promotion unit tests.
- [x] `python -m scripts.export_openapi --check` clean; `@mm/types` fresh.
- [x] Admin typecheck/lint for the runs page + api binding.

## Review

All boxes above done.

Part 1 — `POST /aggregators/runs/trigger` (gated by `reports.sales`, matching the
Runs table) fires `run_daily_once` off a tracked module-level task set
(convention #5), returning at once; refuses with 503 when the ingest is disabled.
The Runs page gained a "Run now" button (loading state, toast, refetch now + a
2.5s follow-up so the just-opened rows show). Schema + generated `@mm/types`
regenerated in the same commit (convention #8).

Part 2 — one-line-per-field normalisation at `upsert_order` (the single seam
every sales order passes through — httpx sweep and Keeta push): naive marketplace
timestamps are stamped Dubai, aware ones pass through untouched. That removes the
+4h "created after the sync" shift on Talabat/Keeta/Noon while leaving the
already-correct Careem/Deliveroo values alone. Existing rows self-heal: a
re-scrape re-upserts the corrected `placed_at`, advancing `updated_at`, which
drives `promote._refresh_order` to reset `created_at`.

Checks: 436 aggregator/provider unit tests + 62 permission/route/app tests pass
(incl. 3 new `_aware_business` tests); OpenAPI `--check` clean, `@mm/types` fresh;
admin `tsc` + eslint clean; CI-pinned `ruff check`/`ruff format --check` clean.

No new env vars/secrets, analytics events, or UI-translation keys — W9/W10 and the
i18n migration rule don't apply.
