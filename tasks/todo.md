# ACTIVE: worker/API cookie-expiry parity fix (2026-09-04)

## Problem
`reauth._channel_needs_reauth` (worker) treated `cookie_expires_at <= now` as dead
for EVERY channel. The API's `session_store.session_unusable_reason` skips the
cookie expiry where `ChannelPolicy.cookie_expiry_advisory` is True (talabat only —
its PerimeterX `_px3` rotates on replay). The worker never got that exemption, so
it called talabat dead on every 2-min heal poll forever. Measured on prod
2026-09-03: 96 re-logins in 16.8h (~137/day) vs a 23/day baseline; talabat 41 of
them, on a metronomic 15-16min cadence set by the 900s success floor.

## Approach
Do NOT copy the policy into the worker — that recreates the duplication that
drifted. The API publishes its authoritative verdict (`unusable_reason`) on every
worker bundle; the worker consumes it and keeps its local derivation only as a
fallback for blue/green deploy skew.

## Plan
- [x] Extract `session_store.unusable_reason_for` so one policy site serves both callers
- [x] `list_worker_bundles` publishes `unusable_reason` per row
- [x] Add `unusable_reason` to the `AggregatorWorkerSession` schema
- [x] Worker `_channel_needs_reauth` prefers the API verdict, falls back locally
- [x] Regenerate OpenAPI + `packages/types` (CLAUDE.md rule 8)
- [x] Tests: API publishes the reason; worker honours it; fallback still works
- [x] Lint + format + test suites green
- [x] Commit + deploy (391c8a9e, deploy run 33842099650 green 2026-09-04 06:10 UTC)
- [x] Verify re-login rate on prod (28-min window, 2026-09-04 06:09-06:37 UTC)

## Result — the churn stopped
28 minutes on the new image: **2 re-logins total** (keeta 1, deliveroo 1 — both
one-time startup heals of genuinely dead sessions) and **zero talabat**. The old
behaviour would have driven ~2 talabat re-logins in that same window.

| | before (09-03) | after (09-04 06:37) |
|---|---|---|
| load avg | 9.88 / 6.53 / 4.74 | **1.54 / 1.90 / 3.05** |
| worker CPU | 110% (at its 1.0 cap) | **0.01%** |
| re-logins | ~137/day (96 in 16.8h) | 2 in 28 min, both one-off startup heals |

The 5- and 15-min averages were still decaying from the pre-fix state when
measured, so the steady state is at or below the 1-min figure. **The
e2-highcpu-4 resize is no longer indicated** — the load was the false-positive
re-login churn, not genuine demand.

## Review
Shipped. Verified on prod immediately after the deploy: `list_worker_bundles`
now publishes a verdict per channel, and **talabat's stored `cookie_expires_at`
(2026-09-04 05:51:51) was already in the past while the verdict read `None`** —
exactly the case that used to say "cookie expired" and drive a headed Chrome.
careem read `needs_bootstrap` (a genuinely dead session the heal loop should
re-login) and deliveroo/keeta/noon read healthy, so the verdict is not
blanket-suppressing — only the advisory cookie is skipped, and only for talabat.

Not done, deliberately out of scope — worth a follow-up:
- `WORKER_MIN_RELOGIN_INTERVAL_SECONDS` is in NONE of the five W9 locations
  (`.env.example`, PRODUCTION.md, deploy.yml, rollback.yml,
  docker-compose.prod.yml) and is absent from the worker container's env. It
  works only because `config.py:153` defaults it to 900, so it cannot be tuned
  without a code change + redeploy. With the parity fix the floor should now be
  near-inert for talabat, which makes this low urgency but still a real gap.
- The success-cooldown skip in `daemon._heal_poll` is silent (bare `continue`).
  The "refreshed recently" log line lives only in the legacy
  `reauth._heal_once`, so grepping for it to measure the guard reports 0 no
  matter what it does. Worth a one-line log if the guard ever needs measuring.
- The e2-highcpu-4 resize decision is deferred until the next 24h measurement.

---

# Central Catalog & Hours Sync — Phase 1 (read + diff, writes gated OFF)

Implements `docs/aggregator-portal-operations-map.md` §"Plan". **Safety rule from the
user: no live changes to any integrator.** Every write path is gated behind a feature
flag that defaults **off**, and even when on, Phase-1 writes run in **dry-run** (plan
only, no portal mutation). This phase delivers the *read side* + *cross-integrator
diff* + the *audit*, plus the model + scaffolding the later write phases will use.

## Guardrails
- Reuse the existing aggregator auth/session/browser infra (`session_store`,
  `aggregator_base`, the bootstrap worker) — do **not** reinvent it.
- Menu writes for the two Foodics branches (Sharjah K001 / Barsha B001) must route to
  Foodics, never the aggregator portal (would fight the GrubTech push).
- All new env vars go through the W9 five-location checklist.
- Money math server-side only; new columns use `Numeric` + `app/core/money.py`.
- New lifecycle columns: `String` + CHECK (rule 6). Migration ≤32-char revision id.

## Tasks

### Backend — model & flags
- [ ] `config.py`: `CATALOG_SYNC_ENABLED` (writes master, default False) +
      `CATALOG_SYNC_READ_ENABLED` (portal menu/hours reads, default False).
- [ ] W9 five-location checklist for both flags.
- [ ] Models `app/models/catalog_sync.py`:
      - `CatalogSyncMap` — per (channel, branch, mm_kind, mm_id) ↔ external ids
        (the missing per-outlet item/category/modifier identity map).
      - `CatalogSyncState` — per (channel, branch, mm_kind, mm_id) → status
        (in_sync|drift|pending|error|absent) + last_synced_at + last_diff JSONB.
      - `AggregatorMenuSnapshot` — per (channel, branch) last-read menu (raw +
        normalized JSONB + source http|browser|manual + fetched_at).
      - `BranchWeeklyHours` — per-day multi-shift canonical MM schedule (holidays
        reuse the existing `branch_holidays`).
- [ ] Sync metadata columns on `products` + `categories`: `sync_to_aggregators`
      (bool, default false) + `sync_channels` (ARRAY, null = all live channels).
- [ ] Migration `171_catalog_sync_scaffold` (additive, guarded).

### Backend — read + diff (safe)
- [ ] `menu_normalized.py` — channel-neutral `NormalizedMenu/Category/Item/
      ModifierGroup/Option` (mirrors `normalized.py`).
- [ ] `fetch_menu(session, outlet)` on `BaseAggregatorClient` (default raises
      `AggregatorUnavailableError`); per-channel best-effort impls gated by the read
      flag; Foodics/GrubOps integrated branches read via existing providers.
- [ ] `catalog_diff.py` — pure diff: MM sync-flagged catalog vs a NormalizedMenu →
      deltas (missing/extra/price/name/desc/modifier). Hours diff too.
- [ ] `catalog_sync.py` service — orchestration w/ per-channel isolation +
      advisory lock + exponential retry (mirror `_sweep_all`/reauth backoff);
      writes hard-gated (503 when off) + dry-run only in Phase 1.
- [ ] Optional gated scheduler `run_catalog_drift_scheduler_forever` (off by default).

### Backend — API
- [ ] `app/schemas/catalog_sync.py` + `app/api/v1/catalog_sync.py`:
      GET status, GET drift report, POST refresh (read-gated), PUT item/category
      sync toggle, POST push (dry-run, write-gated). `require(...)` on all.

### Admin
- [ ] `catalogSyncApi` in `lib/api.ts`; nav entry; `catalog-sync/page.tsx` drift
      report (read-only); per-item "Sync into food aggregators" toggle.

### Tests
- [ ] Unit: diff engine, hours normalization, flag-gating (503 when off), map identity.

### Audit (the operator-facing deliverable)
- [ ] Drive Claude-in-Chrome (operator's live sessions) read-only to capture each
      outlet's current menu + hours; compare to MM catalog + MM hours; write
      `docs/aggregator-catalog-hours-sync-audit.md` — what the sync *would* change
      per outlet × channel if it went through.

### Wrap
- [ ] Regenerate `@mm/types` from OpenAPI (rule 8). Verify migration on throwaway PG.
      Ruff format+check. Commit per W7 (author Hussain Abbasi).

## Status — AUDIT FIRST (user chose "audit first, then build"; pause for review)

**DONE — [docs/aggregator-catalog-hours-sync-audit.md](../docs/aggregator-catalog-hours-sync-audit.md)** (read-only, no writes anywhere).
Live-read MM (prod API, 45 items) + Foodics (131 items/19 cats) + Talabat (Karama
+ Barsha menu + Barsha hours) + Careem (3 outlets + Silicon Oasis catalog + Barsha
hours) + Keeta (Barsha menu + hours) via Claude-in-Chrome.

Headline findings gating the build (revised after operator input):
0. **Deletion of aggregator items not in MM is ALLOWED** — sync is authoritative.
1. **Integrated branches (Sharjah/Barsha) sync via the Foodics `Grubtech` GROUP
   (membership) + `Grubtech` PRICE TAG (aggregator price, separate from product
   price)** — NOT by editing portals, NOT the 131-item catalogue. Group = 9
   subgroups (8 MM cats + New In). Writer touches only those two objects.
2. **Price-tag Price MUST == product Original price** (lockstep on reprice). Only
   current violations: Ramadan 12pc 55→70, 30pc 135→155, Christmas 55→70 (uplifts).
3. Seed `sync_to_aggregators` from **Grubtech group membership**, not
   `sales_channels` (16/45 web-only but live on aggregators).
4. Per-outlet menus diverge (Karama +Ramadan, Barsha +New In, Careem "Boxes"/"&"/no
   Eggless/Fudge off, Keeta recategorised+Unavailable).
5. Hours: same Barsha kitchen = **4 different schedules** (Careem closed Weds). MM
   has no per-day schedule → `BranchWeeklyHours` prerequisite. Hours = first writer.

Build-model updates from this: identity map needs `mm_product → Foodics product_id
→ Grubtech subgroup + price-tag entry` for integrated branches; menu writer targets
the Grubtech group+price tag via Foodics API (confirm write API exists).

**⏸ PAUSED for user review before writing any code/schema (per their instruction).**
Build tasks above are ready to start on approval.

## Review — Phase 1 built (read + diff + model, writes gated OFF)

Shipped, all behind flags (`CATALOG_SYNC_READ_ENABLED` / `CATALOG_SYNC_ENABLED`,
both default false; `CATALOG_SYNC_ENFORCE_PRICE_PARITY` default true):

**Backend**
- `config.py`: 3 flags + **W9 five-location checklist** done (.env.example, PRODUCTION.md,
  deploy.yml, rollback.yml, docker-compose.prod.yml) — `test_compose_env_allowlist` green.
- Models: `CatalogSyncMap`, `AggregatorMenuSnapshot` (`app/models/catalog_sync.py`),
  `BranchWeeklyHours` (branch.py), `sync_to_aggregators`/`sync_channels` on products+categories.
- Migration `171_catalog_sync_scaffold` — verified up/down/up on a throwaway PG; CHECK +
  NULLS-NOT-DISTINCT constraints confirmed.
- `menu_normalized.py` (channel-neutral menu+hours, JSONB round-trip), `catalog_diff.py`
  (name-normalised matching + token-subset rename detection + strict price parity;
  validated on the real captured data), `catalog_sync.py` (MM normalizer, snapshot
  drift, per-target-isolated gated sweep, dry-run `plan_push`, advisory lock …480B),
  `menu_readers.py` (gated reader dispatch; per-portal fetchers land next).
- Router `app/api/v1/catalog_sync.py` (+ schemas) — status/drift/refresh(gated)/push(gated,
  dry-run)/product+category sync toggle; `catalogue.manage`.
- Tests: `test_catalog_sync_diff.py` (10) + full suite **2607 passed**. Ruff format+lint clean.
- `@mm/types` regenerated (rule 8).

**Admin**
- `catalogSyncApi` binding, `Catalog Sync` nav entry, `app/(dashboard)/catalog-sync/page.tsx`
  (read-only per-target drift report + gated refresh). Admin tsc + eslint clean.
  (Not visually previewed — auth-gated internal page following existing patterns.)

**Deferred to next phases (by design):** live per-portal `fetch_menu`/`fetch_hours`
readers (Foodics Grubtech group+price tag first), the hours writer, the menu writer,
and wiring the sync toggle into the product/category edit pages. All slot into the
scaffolding above behind the same flags.

## Follow-up — architecture reuse + complete phases (goal: reuse mappings, no push)

Per operator directive ("reuse existing mapping tables, don't create new ones; figure
out the category/item/modifier/branch mappings; admin config to modify anything"):

- **Dropped the redundant `catalog_sync_map`** (it was defined but never used). The
  identity plumbing is now **reused, not duplicated**:
  - items/options → `external_item_map` (extended with a `category_id` + `category`
    kind so it's the one catalogue map; order reconciliation reads only product/option
    rows and is untouched — verified).
  - branch↔outlet → `aggregator_branch_map` / `foodics_branch_map` (already seeded).
  - live channel item ids → read off `aggregator_menu_snapshot` at write time (no
    stored per-outlet id map to drift).
- Reader now **seeds the shared item-map review queue** (`propose_mappings_from_menu`
  → `external_item_map` proposals for categories + items) — one mapping queue, not two.
- **Phase 2 (hours):** `BranchWeeklyHours` model + **API + admin editor** (per-branch
  weekly schedule) + per-channel normalization (`normalize_hours_for_channel`, Keeta
  ≤5/day) + hours write-plan. Gated.
- **Phase 3 (menu):** diff → concrete write ops resolving each channel id off the
  snapshot (`_build_menu_ops`), Foodics-Grubtech vs portal routing. Gated dry-run.
- **Admin config:** item-mappings queue extended to categories (view/edit/approve);
  catalog-sync page gains the weekly-hours editor; sync toggles + drift already there.
- **Verified locally (no push):** migration up/down/up on throwaway PG; a **real
  DB-backed end-to-end** (build MM menu → set/read weekly hours → store snapshot →
  compute menu+hours drift → dry-run menu & hours push) ran clean; full suite **2610
  passed**; ruff format+lint clean; admin tsc+eslint clean; `@mm/types` regenerated.
- **Still needs live sessions (can't be built/verified locally):** the live HTTP
  readers/writers against Foodics + each portal. Their deterministic MM-side logic
  (normalization, plan, mapping resolution) is implemented + tested; the portal I/O is
  gated and is the production-integration step. Writing it blind would risk the exact
  drift/bugs this refactor removes, so it is left as a clean interface.
