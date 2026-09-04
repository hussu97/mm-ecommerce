# Unified Branch Hours & Holidays — Implementation

Plan: `~/.claude/plans/lets-figure-out-for-unified-bachman.md`

## Part 1 — Data model: one shift per day
- [x] Migration 173: collapse multi-shift rows (MIN opens / MAX closes), add uq(branch_id, weekday)
- [x] Model `BranchWeeklyHours`: docstring + `__table_args__` single-shift constraint
- [x] Move weekly-hours schemas into `app/schemas/pos/branches.py`, validate ≤1/day
- [ ] Verify migration on throwaway Postgres (Docker was down)

## Part 2 — Weekly hours as source of truth (window = derived cache)
- [x] New `services/branch_hours_service.py`: `schedule`, `window_for`, `next_open_window`, `model_weekday`
- [x] Fold weekly-closed weekdays into `closed_dates_for` (no trading_hours rewrite needed — chosen lower-risk design)
- [x] Fix POS holiday gap (`may_auto_accept` takes closed_dates; `accept_order` passes it)
- [x] Unit tests for resolver + service-layout allow-list

## Part 3 — Move hours + holidays UI into Branches tab
- [x] Branch-scoped `GET/PUT /branches/{id}/weekly-hours` in `api/v1/branches.py`
- [x] Relocate schedule read/write into `branch_hours_service`; catalog_sync consumes it
- [x] Migration verified live on throwaway PG (collapse + constraint)
- [x] OpenAPI regenerated
- [x] `branchesApi` bindings; dropped catalog-sync getHours/setHours from admin
- [x] New `BranchWeeklyHours` editor in Branches tab, one-shift/day, "apply to whole week"
- [x] Uses stable toast pattern (no infinite GET loop)

## Part 4 — Daily cron + manual trigger
- [x] `branch_hours_sync.sync_all/sync_branch` + hourly `run_forever` loop (advisory-locked, storefront-only)
- [x] Integrator write seam scaffold `aggregators/hours_writers.py` (registry, all NotImplemented)
- [x] `POST /branches/{id}/sync-hours` + admin "Sync now" button
- [x] No new env var (rides BATCH_DISPATCHER_ENABLED) → no W9 churn

## Part 5 — Holidays across integrators
- [x] Holiday/closed-weekday → closed_dates everywhere; cron calls close_outlet seam
- [x] POS auto-accept now respects holidays

## Part 0 — Live audit (operational, VM) — PENDING
- [ ] Run reads per branch×channel on VM (needs live sessions + CATALOG_SYNC_READ_ENABLED), emit diff report

## Verification
- [x] Unit tests (11 new) + full suite green (2859 passed); types regen; ruff clean; migration verified live on throwaway PG; admin typecheck + lint clean
- [ ] Admin manual check in running app (optional)
