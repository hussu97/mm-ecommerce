# Admin Dashboard Revamp

## Problem
`apps/admin/app/(dashboard)/page.tsx` computes "Today's Orders" and "Today's Revenue"
by fetching only the last 10 orders (`ordersApi.listAll({ per_page: 10 })`) and filtering
them client-side. So the dashboard's daily stats are wrong (capped at 10 orders) and the
revenue is summed client-side — a violation of CLAUDE.md rule #10 (money math is server-side).
The page is also outdated: it ignores POS, aggregator, custom orders, delivery, inventory,
purchase orders, tills, couriers, refunds — all the flows the system now has.

## Approach
Server-side "today" aggregation over ALL orders for the current business day (shop tz),
plus a cross-domain operational snapshot. Then a full rewrite of the dashboard page.

### Backend (`apps/api`)
- [ ] New module `app/api/v1/dashboard.py`, router mounted at `/dashboard`, perm `dashboard.access`.
- [ ] Compute tz-aware UTC day bounds from `business_day_service.shop_today(Asia/Dubai)` (correct
      at the midnight boundary — not `func.date()` which is UTC-dated).
- [ ] Today window over `Order` (created_at in window):
      - summary: orders, revenue (money()), avg_order_value, delivered count, + growth vs
        the same window yesterday.
      - breakdowns: by_status, by_channel (source), by_fulfillment (delivery_method),
        by_payment (payment_method).
- [ ] Live operational counters (current open state): awaiting-action, out_for_delivery,
      undelivered, payment_failed (today), refunds (today: count+amount), open custom orders,
      custom orders due today, low-stock items, pending purchase orders, open tills, active couriers.
- [ ] Response schemas in `app/schemas/dashboard.py` (rule #11 — schemas live in app/schemas).
- [ ] Register router in `app/api/v1/router.py`.
- [ ] Regenerate contract: `python -m scripts.export_openapi` + `pnpm --filter @mm/types generate`.
- [ ] Unit test in `apps/api/tests/` seeding orders across today/yesterday and asserting the aggregates.

### Frontend (`apps/admin`)
- [ ] `dashboardApi.today()` binding in `lib/api.ts`; `DashboardToday` types in `lib/types.ts`.
- [ ] Rewrite `app/(dashboard)/page.tsx`: hero metrics (today, server-side), order pipeline,
      needs-attention tiles, channel/fulfillment/payment mix (CSS bars — no new dep),
      recent-orders feed, quick actions. `Promise.allSettled` + per-section errors. Auto-refresh + "as of".

### Verify
- [ ] `ruff check/format`, `pytest`, `export_openapi --check` (api job)
- [ ] admin `lint` + `tsc --noEmit` + `build`; `@mm/types check:fresh` + test (web job)
- [ ] Push to feature branch `claude/admin-dashboard-redesign-288sfk`, open PR to main, drive CI green.

## Review / results
- New endpoint `GET /api/v1/dashboard/today` (`dashboard.access`) aggregates **every** order of
  the shop's local day server-side — fixing the last-10-orders cap and the client-side revenue sum.
  "Today" is resolved to exact UTC bounds from the shop timezone, correct at the midnight boundary.
- Returns: headline summary (revenue, orders, delivered, AOV) with growth vs the same window
  yesterday; by-status / by-channel / by-fulfilment / by-payment breakdowns; and a live ops snapshot
  (out-for-delivery, undelivered, payment-failed, refunds, custom orders due/open, low stock, pending
  POs, open tills, active couriers).
- Dashboard page rewritten: headline cards, a Needs-Attention grid linking into each flow, today's mix
  bars, an order-status strip, quick actions and the recent-orders feed. Auto-refreshes every 60s with
  an "as of" stamp; `Promise.allSettled` so one failed call doesn't blank the page.
- Contract regenerated (`openapi.json` + `@mm/types/generated.ts`).
- Verified locally: API ruff/format/openapi-check + full pytest (2346 passed, +8 new); admin
  lint/tsc/test/build; web lint/test/tsc/build + `@mm/types` fresh & tests. All green.

## Note on branch
Session policy designates branch `claude/admin-dashboard-redesign-288sfk` and forbids pushing to
a different branch. Direct-to-main also skips review and fires the production deploy immediately.
So: push to the feature branch + PR; merging the PR is what triggers `deploy.yml`. CI ("deploy is
green") is driven green on the PR.
