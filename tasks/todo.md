# Capture GrubOps-side cancellations + reason

Order `AGG-20260825-024` was cancelled in GrubOps (`cancelReason: TOO_BUSY`) but MM
never saw it — it is frozen at `arrived_at_pos` because the poll only re-checks orders
still inside `getOrderSummaryList`'s single most-recent window. Once an order ages out
of that window, a later GrubOps cancellation/completion is missed forever.

## Plan (Full fix; reason shown in Marketplace panel only)

### Backend — detection
- [ ] `grubops_orders_service.sweep_open_orders(db, seen_ids)`: re-poll open aggregator
      orders (`created`/`confirmed`/`arrived_at_pos`) not in this tick's summary, via
      `getOrderInfo`, reconcile through `ingest`. Bounded, mirrors `sweep_auto_close`.
- [ ] Wire into `grubops_orders.sweep_once` (collect `seen` ids; own try-block).

### Backend — reason capture
- [ ] Migration `150_agg_cancel_reason`: add `orders.aggregator_cancel_reason`
      (`String(60)`, nullable, unconstrained — provider-verbatim). No backfill.
- [ ] `Order` model: add column + docstring.
- [ ] `_cancel_reason(info)` helper (header.cancelReason → history description fallback).
- [ ] CANCELLED branch of `_apply_status`: set the column + pass `note=` to `acting_as`.
- [ ] `OrderResponse.aggregator_cancel_reason`; regenerate `@mm/types`.

### Frontend — admin
- [ ] `apps/admin/lib/types.ts`: add `aggregator_cancel_reason`.
- [ ] Order-detail Marketplace panel: show humanized reason in the read-only note.

### Tests
- [ ] reason captured on order + status-event note.
- [ ] `sweep_open_orders` catches an order cancelled after leaving the window.

### Live repair
- [ ] `AGG-20260825-024` self-heals on the first tick after deploy (sweep re-polls it).

## Review

Done. Root cause was a detection gap, not a mapping gap: `getOrderSummaryList` is
a single most-recent window, so an order lingering at `arrived_at_pos` aged out
before GrubOps cancelled it (12:18, ~47 min in) and the loop never re-checked it.

- `sweep_open_orders(db, seen_ids)` re-polls open aggregator orders the summary
  dropped and reconciles any that moved through the existing `ingest` — fixing
  missed cancellations *and* completions. Wired into `sweep_once`.
- `_cancel_reason(info)` reads `orderHeader.cancelReason` (→ history fallback);
  the CANCELLED path stamps `orders.aggregator_cancel_reason` and passes the
  reason as the status-event `note`. Migration `150_agg_cancel_reason` (verbatim
  String, no CHECK — canon rule 6). Foodics write-back stays suppressed (actor is
  `aggregator`), so no double-cancel.
- Admin Marketplace panel shows a humanised "Cancelled by marketplace — Too busy".
- `arrived_at_pos → cancelled` is a valid transition, so `AGG-20260825-024` flips
  on the first tick after deploy. `packed` stays refused by the ingest (unchanged).

Tests: 2127 api unit passed (+6 new); admin type-check + eslint clean; single
alembic head; `@mm/types` regenerated.
