# Aggregator generalization + runs table + richer report — plan (round 4)

Goal (2026-08-28, Fri): generalized re-runnable scraper/promoter over arbitrary
business-date ranges with bulletproof dedup; clean slate of scraped+promoted
data (keep grubops/foodics/website); rerun all 5 aggregators for Thu+Fri Dubai;
order-display audit so promoted == grubops everywhere; admin "aggregator runs"
table; richer daily sales report (order / statement+line / payout tabs + summary,
frozen to current business date).

## DB ground truth (verified live on prod 2026-08-28)

- Aggregator tables: aggregator_account, aggregator_branch_map, aggregator_order,
  aggregator_order_item, aggregator_payout, aggregator_reconciliation,
  aggregator_session, aggregator_statement, aggregator_statement_line,
  **aggregator_sync_run** (already exists!).
- Row counts: order 96, order_item 96, statement 65, statement_line 13973,
  payout 48, reconciliation 96, sync_run 37.
- **`aggregator_sync_run` already models runs**: columns channel, mode
  (sales/finance/backfill), from_date, to_date, status
  (planned/running/completed/failed/partial), started_at, finished_at,
  stats jsonb, error. reconciliation.run_id FKs to it. → EXTEND + surface, don't
  rebuild.
- **Cleanup discriminator — CORRECTED (critical)**: `aggregator_order.mm_order_id`
  links each scraped order to an MM order, BUT for Barsha/Sharjah promotion links
  to an EXISTING GrubOps-owned order and only overlays commission (promote.py
  571-583). So "reachable via aggregator_order.mm_order_id" is NOT sufficient —
  must exclude any order that also has a grubops_order_map row.
  - Verified live: 96 scraped rows linked; **57 point at GrubOps-owned orders**,
    only **39 are promoted-only (safe to delete)**. Naive delete would have
    destroyed 57 grubops orders.
  - **SAFE delete set** = `orders.id IN (SELECT mm_order_id FROM aggregator_order
    WHERE mm_order_id IS NOT NULL) AND orders.id NOT IN (SELECT mm_order_id FROM
    grubops_order_map WHERE mm_order_id IS NOT NULL)` → 39 orders.
  - Then truncate scraped tables (aggregator_order/item/statement/statement_line/
    payout/reconciliation/sync_run). FK SET NULL unlinks grubops orders'
    mm_order_id + clears the commission overlay source; re-scrape re-overlays.
    Grubops orders + website/cashier untouched.
  - Distribution: Talabat 206 MM (30 scrape / 176 grubops), Keeta 2.0 115 (24/91),
    Noon Food 71 (0/71 grubops), Careem 20 (20/0 scrape), Deliveroo 20 (3/17),
    Noon 19 (19/0 scrape).

## Backend facts (from explore agent 1 — apps/api)

- **Dedup already solid**: every persist is `pg_insert(...).on_conflict_do_update`
  on channel-scoped natural keys — order `(channel,external_order_id)`, item/line
  `(channel,source_key)`, statement `(channel,statement_id)`, payout
  `(channel,transfer_id)`, reconciliation `(channel,external_order_id)`.
  `_PRESERVE_IF_NULL` COALESCEs a thin sales re-pull's NULLs so it can't erase
  settlement figures; `_touched_at` advances updated_at only on a real change so
  no-op re-runs don't re-trigger promote/reconcile. Re-running a range is safe
  today. Only double-count risk = stock decrement, already once-on-create +
  draw_stock/stock_cutoff guarded. → mostly VERIFY + document, not rebuild.
- **Generalization seam**: `_sweep_channel` (ingest.py 626-727) derives the window
  internally from AGGREGATOR_LOOKBACK_DAYS; `sweep_channel_once` (730-752) takes
  lookback_days/hours but NOT explicit from/to. Add business-date from/to here.
- Providers get real datetimes; **Deliveroo + Talabat treat API end/to as
  EXCLUSIVE (+1 day internally)**, Careem endDate inclusive, Noon filters
  inclusive both ends. Keeta + Deliveroo-invoice arrive via PUSH endpoints, not
  the httpx sweep (Keeta absent from PROVIDERS).
- Promotion convergence: promoted & grubops orders resolve to the SAME MM row via
  partial unique idx `uq_orders_source_external_reference` on
  `(source='aggregator', external_reference)`. created_at already = placed_at.
  Promoter labels via `CHANNEL_GRUBOPS_LABEL` (promote.py 559) — but prod shows
  "Noon" vs grubops "Noon Food" → fix that map entry.
- **Order origin has NO sub-discriminator column**: grubops vs promoted both
  `source='aggregator'`, same aggregator_channel, same AGG- number series. Only
  the side-table (grubops_order_map vs aggregator_order) tells them apart — which
  is exactly why they already display identically (audit should confirm, not add).
- `aggregator_sync_run` has no natural key (every trigger = new row, good for an
  audit log); stats jsonb is the place to record retrieved/promoted/existing/%.
- **Order origin columns**: orders.source ('aggregator'|'online'|'cashier'),
  order_type ('delivery'|'pickup'|...), aggregator_channel (Talabat, Keeta 2.0,
  Noon Food, Careem, Deliveroo, **Noon**).
  - AUDIT ITEM: promoter uses channel label "Noon" but grubops uses "Noon Food"
    for the same marketplace → normalize so promoted == grubops in every UI.

## Report + cleanup facts (from explore agent 3)

- **Daily report = the Daily Sales EMAIL** (`app/services/pos/daily_sales_email.py`):
  branch×channel xlsx matrix, reads ONLY `orders` (+order_deliveries), VAT-incl
  `Order.total`, delivered-only, keyed on `Order.business_date` (String(10),
  branch-tz trading day frozen at write, default Asia/Dubai, 04:00 cutoff via
  business_day_service). Nightly loop rides BATCH_DISPATCHER (storefront api only),
  advisory-locked, sends once last branch close+45m passes; _CATCHUP_DAYS=3.
  Manual trigger `POST /pos/reports/sales/daily-email` (schemas/reports.py).
  Frontend = "Email Report" tab in apps/admin pos-reports (EmailTab.tsx).
  → "tabs" the user wants = xlsx SHEETS: Summary / Orders / Statements+Lines /
    Payouts, now pulling aggregator_statement/line/payout (currently ignored),
    frozen to one business date.
- **Cleanup template = migration `161_agg_clean_slate.py`** (current HEAD;
  down_revision 160_agg_stmt_invoice). TRUNCATE ... RESTART IDENTITY CASCADE over
  scraped set {reconciliation, statement_line, order_item, payout, statement,
  order, sync_run}; KEEPS account/session/branch_map; never touches orders/
  grubops/foodics. It does NOT delete promoted-only MM orders (they "re-converge"
  on next promote). → For the on-demand clean slate build a callable PURGE
  SERVICE (scripts/ or service fn) that also deletes the 39 promoted-only orders;
  a migration runs once so it's wrong for a repeatable op.
- No standalone prune script exists; log_retention only sweeps audit/email/webhook.
- Statement/line/payout: only rolled-up `GET /aggregators/reconciliation/settlement`
  exists (settlement_reconcile.py). NO raw per-line or per-payout list endpoint,
  NO frontend for them → net-new for report tabs + (maybe) admin.
- Migrations: apps/api/alembic/versions/, next id `162_<slug>` (≤32 chars).
- Money via app/core/money.py; email currently uses local round() — follow §10.

## Keeta wrinkle
Keeta is PUSH-only (bootstrap worker pulls in-page via mtgsig, pushes to
ingest_keeta_payloads/finance). httpx sweep EXCLUDES it. A date-range rerun for
keeta must drive the worker (keeta_pull.py) with the range, not the API sweep.

## Order-display parity facts (from explore agent 2) — THE pivotal decision

- grubops vs promoted are ALREADY row-identical: both `source='aggregator'` +
  `aggregator_channel`. Every source-gated surface treats them the same (admin
  orders list [orders.py:309 get_all_admin, source-only], order detail, dashboard
  `_breakdown`, analytics/commerce, courier badge `CourierBadge.for_order`). NO
  grubops-vs-promoted branch exists in any UI → nothing to remove there.
- **The ONE divergence = POS attachment / is_pos.** GrubOps calls
  `pos_order_service.attach_aggregator_order` (is_pos=True, pos_status, check_number,
  business_date, opened_at). Promotion NEVER touches POS (promote.py:18).
- So promoted orders are EXCLUDED from every is_pos-gated surface:
  - `pos_orders.py:227` (POS order list — is_pos.is_(True))
  - `pos_reports/_base.py:34` `_scope` + `_COMPLETED_SALE` :63-66 (ALL POS reports)
  - `operations.py:434/497/633/645` (live board + stock)
  - **`daily_sales_email.py:179` (the daily report itself!)** — promoted orders
    are not in the daily sales report today.
- DSO/Karama are promotion-OWNED (no grubops) → their sales appear NOWHERE in
  POS reports/daily email today unless promotion attaches to POS. Barsha/Sharjah
  are grubops-attached already.
- FIX options: (a) promotion attaches to the register like grubops, with the
  order's TERMINAL pos_status (delivered→closed, cancelled→void) + business_date
  from placed_at, so it's structurally identical everywhere by construction and
  never shows as "open" work; (b) widen ~6 is_pos predicates to source=='aggregator'
  (fragile, many touch points, NULL pos_status/business_date break groupings).
  → RECOMMEND (a): one change, robust parity by construction. CONFIRM w/ user
    (attaches rows to live registers = hard to reverse).

## Admin runs-table facts (agent 2)
- Aggregators tab group `apps/admin/app/(dashboard)/aggregators/`, sub-nav
  `AggregatorTabs.tsx:17-21` (Reconciliation/Mappings/Logins). Add `Runs` tab +
  `runs/page.tsx` following reconciliation page pattern.
- `AggregatorSyncRun` model is unexposed end-to-end. Need: `GET /aggregators/runs`
  in aggregators.py + `AggregatorSyncRunOut` in schemas/aggregator.py + regen
  @mm/types + `aggregatorRunsApi.list` in admin lib/api.ts + page.
- Admin table stack: `useApiList` (server mode, perPage default 50), `DataTable`,
  `Pagination` (options [50,100,200,500,1000,2000] — matches W8). request() +
  buildQs in lib/api.ts. Contract types from @mm/types (rule 8).

## DECISIONS (user-confirmed 2026-08-28)
- Parity = **attach promoted orders to POS** (terminal pos_status + business_date
  from placed_at), so identical everywhere by construction.
- Daily report = **Excel sheets** in the existing Daily Sales email: Summary /
  Orders / Statements+Lines / Payouts, frozen to one business date.

## Execution order (code before destructive data ops)

**A. Backend code** (one deploy)
  A1. Generalize sweep: add `from_date`/`to_date` (date) to `_sweep_channel` +
      `sweep_channel_once` → since=start-of-from_date(Dubai), until=end-of-to_date.
  A2. Orchestrator `run_range(channels, from_date, to_date, modes)`: per channel×
      mode sweep + promote(range) + reconcile, each wrapped in aggregator_sync_run
      with RICH stats {sales_retrieved, statements, payouts, invoices, promoted,
      existing, not_promoted, pct_*}. Idempotent, multi-run/day safe.
  A3. Promotion attaches to POS: promote_order/_build_order call
      pos_order_service.attach_aggregator_order with TERMINAL pos_status +
      business_date from placed_at (DSO/Karama promotion-owned; Barsha/Sharjah
      already grubops-attached → overlay only, no double attach).
  A4. Channel label fix: CHANNEL_GRUBOPS_LABEL noon → "Noon Food" (match grubops).
  A5. Tests (dedup idempotency, from/to window, attach, label). Commit+deploy.

**B. Purge service + run on prod** (after A deployed)
  - `maintenance.purge_scraped_data(delete_promoted_orders=True)`: delete 39
    promoted-only MM orders (SAFE set, children CASCADE) + TRUNCATE scraped
    tables. Run once on prod.

**C. Rerun** all 5 (careem/deliveroo/talabat/noon via httpx run_range; keeta via
   worker push) for Thu 2026-08-27 + Fri 2026-08-28 Dubai. Validate + promote.

**D. Admin runs table**: GET /aggregators/runs + AggregatorSyncRunOut schema +
   regen @mm/types + aggregatorRunsApi + runs/page.tsx + Runs tab. Deploy.

**E. Daily report xlsx sheets**: rework daily_sales_email to emit Summary/Orders/
   Statements+Lines/Payouts sheets from orders+aggregator_statement/line/payout,
   frozen business date. Deploy.

**F. Audit**: confirm promoted now appear in POS reports/daily email/live-ops
   identically to grubops; no grubops-vs-promoted distinction remains.

## PROGRESS
- [x] A — generalized run_range + POS attach + Noon Food label (commit 3a6b6cf)
- [x] A-extra — careem honours date range + Dubai business_date (83cae0d)
- [x] A-extra — keeta status map accepts "completed" word (8f57cb8)
- [x] B — purge service (3e6d003); ran on prod: 39 promoted-only deleted, grubops
      412 intact, website/cashier untouched
- [x] C — reran all 5 for 27-28 Dubai; VALIDATED all scraped==promoted==on_register:
      careem 3/6, deliveroo 3/9, keeta 24/27, noon 19/21, talabat 30/53.
      Matches sheet (talabat 30=29net+1void, noon19, deliveroo3, keeta24~25, careem3).
      Careem needed re-login+warm (session dead); keeta trimmed to range.
- [x] D — admin Aggregator Runs table (bd11206): GET /aggregators/runs + schema +
      @mm/types + Runs tab + page. Deployed (Vercel green). API verified returning runs.
- [x] E — daily report xlsx sheets (47f0ae9): Summary(+totals)/Orders/Statements/
      Statement Lines/Payouts, scoped to frozen business date. Verified on prod:
      Aug-27 → 96 orders; Aug-22 → 7 statements + 196 lines.
- [x] Wider finance run (Aug 8-28): 31 statements, 31 payouts, 4845 lines populated
      so the report's finance tabs + reconciliation have data (sales 27-28,
      finance ~15d — matches the original "sales 1d, finance 15d" intent).
- [x] F — parity audit PASSED: aggregator orders for 27-28 both origins 100%
      on_register — promotion-only 72/72, grubops-owned 161/161. Promoted orders
      now appear in POS reports/daily email/live-ops identically to grubops.

## DONE — all phases complete, all deploys green.
