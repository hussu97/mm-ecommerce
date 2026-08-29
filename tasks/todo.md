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

## Follow-up — date-range trigger + fixing the Aug 27–28 rows

Asks: (1) "Run now" should take a user-specified date range; (2) existing Aug 27–28
scraped orders have the wrong dates — fix them; (3) would a re-run auto-update them?

Answer to (3): **yes, but only for a run that covers those business dates.** The
default daily pass re-scrapes just the last day, so it never re-touches Aug 27–28.
A *range* backfill re-scrapes them → the now-normalised `placed_at` changes the
stored value → `updated_at` advances → `promote_channel` (whose window is the wide
settlement lookback, so it reaches back that far) re-promotes → `_refresh_order`
resets `created_at`. So the range trigger is both the feature and the fix.

- [x] `trigger_range_in_background(from, to, channels)` in `ingest.py` (shares the
      tracked-task launcher with the daily trigger); wires to the existing
      `run_range` backfill (scrape → promote → reconcile).
- [x] `POST /aggregators/runs/trigger` now takes an optional body
      (`AggregatorRunTriggerIn`: `from_date`/`to_date`/`channels`). No dates → the
      recent daily pass; both → a range backfill. Validates both-or-neither,
      from ≤ to, ≤ 92-day span, known channels (all `BadRequestError`).
- [x] Admin Runs page: From/To date inputs next to the button (label flips to
      "Backfill range"), with a one-line explainer; client-side both-or-neither +
      order guards before the call.
- [x] `@mm/types` regenerated; 7 new endpoint tests (dispatch + every validation
      branch) added; ruff/format/tsc/eslint/openapi-check all clean.

Caveat surfaced to the user: Keeta is push-only — `run_range` records it skipped,
so Keeta rows can't be re-pulled from the console (they'd need a bootstrap re-push).
The user described these as "scrape side", i.e. the httpx channels, which the range
run fully covers.

## Follow-up 2 — Keeta backfill (the push-only gap) + promote-field audit

Two questions from the user:

**(1) Were Keeta timestamps also wrong?** Yes. Keeta's `_parse_datetime`
(`_as_business_local`) always returns a *naive Dubai wall-clock*, so its
`placed_at` had the same +4h-stored-as-UTC bug. Its `business_date` was always
derived from the Dubai `.date()`, so that stayed correct (which is why the
date-range scoping below is safe). The `upsert_order` normaliser now fixes Keeta
going forward, but the merged backfill couldn't reach existing Keeta rows because
Keeta is push-only (`run_range` recorded it "skipped").

Fix — re-normalise from stored `raw` instead of skipping:
- [x] `keeta_provider.order_from_raw()` — public seam to re-parse one saved payload.
- [x] `ingest._renormalize_stored()` — for a push-only channel, re-parse each
      `aggregator_order.raw` in the date range and re-`upsert_order` (idempotent,
      raw is the immutable source). Changing `placed_at` advances `updated_at`
      (via `_touched_at`), so the run's promote step re-promotes and resets
      `created_at`.
- [x] `_run_range_channel`: push-only channels now take the re-normalise path and
      fall through to promote/reconcile instead of failing. `record_success` is
      skipped for them (no scrape session to credit). So "Backfill range" over
      Aug 27–28 now fixes Keeta too — no bootstrap re-push needed.

**(2) Does promote overwrite all fields / clobber the grubops ref?** No.
- `_refresh_order` (promotion-owned orders) touches only money, customer
  (fill-only), `created_at` (when placed_at differs), the actual-fee overlay, and
  status — not items, branch, business_date, or external refs.
- For **grubops-owned** (Barsha/Sharjah) orders, `promote_order` returns early
  (the `_branch_has_grubops` → `_find_mm_order` path): it sets the aggregator→mm
  link and overlays actual fees only — it never touches the grubops order's
  `created_at`/items/status. Those orders' `created_at` comes from the GrubOps
  ingest (`_parse_ts`, already tz-correct), so they were never affected and the
  re-promote leaves them intact. The `(source, external_reference)` convergence
  key is the safety net that prevents the historical Keeta duplicate-order issue.

- [x] Tests: `order_from_raw` round-trips a Keeta payload to the correct instant;
      `_renormalize_stored` re-ingests each stored row; `_push_order_parser` maps
      Keeta only. ruff/format/openapi-check clean; 82 ingestion tests pass.

## Follow-up 3 — robustness: Talabat GraphQL auth-error classification

Backfill of Aug 27–28 surfaced Careem + Talabat failures on expired sessions (401).
- **Careem** hits a real HTTP 401 → the base already raises `AggregatorAuthError`
  ("session no longer authenticates"). Correctly classified; it just needs a
  re-login (bootstrap). No code change.
- **Talabat** buries its `vp-report-builder` auth failure inside a GraphQL **200**
  body (`errors`: 401 Unauthorized / TOKEN_EXPIRED), so the base's HTTP-status check
  never saw it — `_graphql` raised the generic `AggregatorUnavailableError`, so the
  sales sweep neither flagged the session for re-login nor read clearly. Only the
  finance path (a real 401) detected the dead session.

Fix (Talabat only):
- [x] `_graphql_errors_are_auth()` — scans a GraphQL `errors` payload for
      token-expiry / unauthorized / unauthenticated markers (no bare "401", which a
      store id could carry, so a real VALIDATION_ERROR still reads as unavailable).
- [x] `_graphql` raises `AggregatorAuthError` for those, so the sweep flips the
      session to `needs_bootstrap` and the run row reads "session token expired —
      needs a re-login", consistent with the finance path and with Careem.
- [x] Tests for the classifier and `_graphql` auth-vs-unavailable behaviour.

Note: this improves *detection* — it can't re-authenticate a dead session. Both
Careem and Talabat still need a headed re-login (bootstrap worker) to recover; the
fix makes the failure actionable and correctly routes it into that flow.
