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
