# Deploy pipeline speed (2026-08-30)

Baseline: GCP job `33300260803` was 18.6 min (pytest 98s + API image 67s +
bootstrap image 104s + VM 13.3 min). VM time was Playwright pull 4.5 min,
compose re-pulling :latest three times, N+1 i18n seed, 15s×2 drain, 30s×2 stop.

- [x] Batch i18n seed (one SELECT, not one per key)
- [x] `pull_policy: missing` on API slots; stop compose re-pulls
- [x] Split test-api || build-api || build-bootstrap; promote :latest after tests
- [x] Drop `--cov` on deploy pytest; drop bootstrap VM pull; drop deploy.yml from bootstrap/web/admin filters
- [x] Faster healthcheck/drain/stop; flock on `/tmp/mm-aggregator-warm.lock`
- [x] Skip journald restart, certbot compose-run, builder prune on API-only deploys
- [ ] Push, watch e2e deploy, record timings vs 18.6 min

---

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

## Follow-up 4 — Noon duplicate (convergence), warmer auto-relogin, Talabat re-apply

Three items bundled (PR after #42, which merged Keeta-only again).

### Re-applied
- [x] Talabat GraphQL token-expiry → AggregatorAuthError (didn't reach main via #42).

### #2 — Noon GrubOps↔promote duplicate (convergence)
Root cause: for Noon the two write paths key `orders.external_reference` on
DIFFERENT ids — GrubOps ingest uses GrubTech's `externalId` (the short customer
code, e.g. "2253"), promote uses Noon's long `orderNr`. So convergence missed and
the order was filed twice. Noon's OMS payload carries the short code as `orderRef`
right next to `orderNr` — the shared key.
- [x] `aggregator_order.display_ref` column (migration 162) + model + StandardOrder.
- [x] Noon provider captures `orderRef` → `display_ref` (OMS + merge).
- [x] `upsert_order` persists it (+ `_PRESERVE_IF_NULL`).
- [x] Convergence matches the short code too, EXACT + SCOPED so it can't false-merge:
      `_find_convergence_order` adds `external_reference == display_ref` scoped to
      the same branch + Dubai business day (read off `created_at`, since the
      GrubOps-made order has no `business_date`); `reconcile._find_mm_order` matches
      either id under the channel label; the GrubOps adopt block matches
      `aggregator_display_code` scoped to branch + placed day (reverse direction);
      `_build_order` sets `aggregator_display_code` from the short code.
- [x] Tests: Noon captures orderRef; convergence/`_find_mm_order` SQL keys on both
      ids; no short-code clause without display_ref.

VALIDATION (user runs on prod — self-hosted DB, not reachable from here): confirm
`aggregator_order.raw->>'orderRef'` == `grubops_order_map.external_id` for Noon.
The fix is safe pre-validation: exact scoped matches, no-op if the ids don't relate.

CLEANUP (user runs on prod): delete the aggregator-promote duplicate row
`AGG-20260829-038` (SQL provided in chat: remove the MM order + items/taxes + POS
order, restore any drawn stock, null the aggregator_order → mm link).

### #1 — warmer auto-relogin (apps/aggregator-bootstrap)
Re-login is the external worker's job; its cron only warmed live sessions and left
a dead one for a human. `needs_bootstrap` never triggered a re-login.
- [x] `warm-sessions` now defaults `--auto-relogin`: a warm that hits a dead session
      escalates to the stored `login --auto` (email/password + Graph-mailbox OTP),
      reusing `login_with_account`; only a login needing a human (captcha/passkey,
      no mailbox) falls through. Cron comment updated.
- [x] Tests for the escalation + the login/push + human-needed + no-account paths.
Note: untested against a live headed browser (can't run Chromium+Xvfb here); logic
reuses existing, proven login/push functions.


---

# Aggregator auth & ingestion redesign — make autonomous runs work 100% (2026-08-30)

Branch: `aggregator-auth-redesign`. Plan: `~/.claude/plans/this-repo-contains-some-merry-twilight.md`.
Root causes + live-VM evidence: memory `aggregator-autonomous-401-root-causes.md`.
Manual runs work; the hourly autonomous refresh 401s — an architecture problem
(single shared flock + status-column coordination + reactive heal with ~7 failure modes).

## Phase 0 — Acute stopgaps [W] (revertible) — DONE (committed + on main + verified on VM)
- [x] `docker-entrypoint.sh`: heal-sessions now runs under xvfb (heal can re-login anti-bot) — VERIFIED: manual heal recovered noon/talabat/careem/deliveroo headed
- [x] `deploy/aggregator-warm.cron`: `timeout -k 30` around every `docker compose run` (reap hangs, free flock); single shared lock kept (1 Chrome = fits RAM)
- [x] **CRITICAL: fixed unescaped `%` in the heal cron** — cron.d turns a bare `%` into a newline, truncating the old `printf "%s"` gate at `printf "` so the every-2-min heal ran NOTHING for hours (the real "warming cron runs but auth 401s" cause). Rewrote the gate to pass the body via `HEAL_BODY` env var (no `%`). VERIFIED live: heal tick now logs "all sessions live, skip". Regression test added.
- [x] **Found + worked around the stale-bootstrap-image deploy bug**: `aggregator-worker` uses `pull_policy: missing`, so a rebuilt `:latest` never reaches the VM — Phase 0 entrypoint fix (and prior worker changes) never deployed. Manually pulled on VM; Phase 3 daemon (`up -d` + `pull`) fixes it permanently.

### E2E validation on VM (2026-08-30 ~23:15 UTC)
- All 5 channels live and sweeping: noon/talabat/keeta (autonomous 22:15 warm + keeta cron), careem + deliveroo (headed heal). Deliveroo sales = 4 orders, careem = 3 orders after headed re-login. Deliveroo sales confirmed to need headed cf_clearance (Phase 2 org fix + headed cookie = fixed).

## Phase 1 — Leader election [A] — DONE (commit)
- [x] `ingest.py`: `_SCHEDULER_LEADER_LOCK_KEY` (…480A) + `run_aggregator_schedulers_forever()` (reuse advisory_lock.held lifetime hold; poll = `_LEADER_POLL_SECONDS` module constant, no new env var)
- [x] `app_setup.py`: wire the single supervisor; it finally-cancels its children (8s shutdown cap preserved)
- [x] 3 unit tests (leader runs both loops / standby never ticks / standby→leader promotion)

## Phase 2 — Deliveroo mint fix [A] — DONE (commit)
- [x] removed `_DEFAULT_ORG_ID`/`_DEFAULT_RESTAURANT_IDS` + fallbacks; org from account.extras, outlets from branch map; `_augment_from_db`/`_org_id`/`_restaurant_ids` raise `AggregatorUnavailableError` if absent
- [x] deleted the `companies[0].id` org override in `_login` (the wrong-scope 401 cause); `_login` returns None on failure (not the stale `previous`); carries the captured cf_clearance/anti-bot cookies forward on mint
- [x] enhanced mint log (org + outlet count + carried cookie names) is the on-VM confirmation signal; updated `test_deliveroo_provider.py` + 3 new `_login`/`_augment` tests

## Phase 3 — Worker daemon: queue + hard timeout + resident Xvfb [W] — BUILT on branch `aggregator-auth-redesign`, NOT deployed
- [x] `serve)` entrypoint (resident Xvfb); `queue.py`; extracted `reauth.py`; `daemon.py` (commit e5a93538, merged 31ec1c90)
- [x] `browser.kill_live_chrome()` + `_LIVE_CHROME` registry; `run_job_guarded` hard timeout
- [x] in-process Dubai-wall-clock scheduler; retire cron+flock; compose worker always-on (`restart: unless-stopped`, `command:["serve"]`, heartbeat healthcheck); deploy.yml `pull`+`up -d` (also fixes the pull_policy:missing stale-image bug)
- [x] worker suite green: 84 passed, ruff clean; unit tests for queue + daemon (timeout→kill, dispatch routing, heal poll, Dubai next_due)
- **NOT MERGED TO MAIN / NOT DEPLOYED — remaining before it can ship:**
  - [ ] Rework `scripts/cutover-backend.sh`: remove the dead flock/warm-lock machinery (daemon takes no flock); still `docker stop` the worker to free RAM during cutover, but ensure it is `up -d`'d again by EVERY caller (deploy.yml done; verify rollback.yml + deploy.sh)
  - [ ] Update the 5 obsolete API cron-model tests (they read the now-deleted cron): `test_aggregator_heal_gate.py::{test_cron_heal_python_parses_and_flags_not_live,test_cron_heal_line_has_no_bare_percent,test_cron_antibot_warm_omits_careem}` + `test_blue_green_cutover.py::{test_cutover_flock_is_writable_by_the_deploy_user,test_aggregator_warm_cron_three_clocks_and_curl_gated_heal}` → repoint to the daemon model or retire (daemon behavior already covered by `test_daemon.py`). Keep `test_decommission_never_drops_ecommerce_tables` intent.
  - [ ] **VM validation (live browser) before flip**: build the bootstrap image, run `docker compose run --rm aggregator-worker serve` under the resident Xvfb, confirm it starts, heartbeats, heals a killed session, and stays < 2GB with one transient Chrome; then validate the deploy/cutover stop→up-d interaction in a low-traffic window.
- NOTE: main still runs the cron model (Phases 0-2 + hotfix), which is verified working + self-healing — so prod is safe while Phase 3 awaits a validated cutover.

## Phase 4 — DB-as-truth profile fix [W]
- [ ] `browser.py` probe_channel: always use hydrated storage_state (drop `.chrome` preference)

## Phase 5 — Per-channel policy + one retry helper [W]+[A]
- [ ] `services/aggregators/policy.py` + worker `channels/policy.py`; `next_backoff()`; absorb scattered constants; golden test

## Phase 6 — Robust liveness [A]+[W]
- [ ] migration `168_agg_liveness` (`last_verified_at`, `consecutive_failures`); model + session_store; worker stamps; `@mm/types` regen

## Phase 7 — Reauth contract + careem coverage [A]+[W]
- [ ] `request_heal`; simplify `_await_reauth`; worker consumes heal queue; careem login_method fix

## Phase 8 — Observability + alerting [A]+[W]
- [ ] `_log_lifecycle`; extend `_log_health`; dead-beyond-threshold alert; worker `report_needs_human`

## Phase 9 — Deploy finalization [W]
- [ ] deploy.yml: drop cron/flock, add compose pull+up; healthcheck; delete cron file

## Review (redesign)
_(filled in as phases land)_
