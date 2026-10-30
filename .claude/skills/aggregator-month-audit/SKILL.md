---
name: aggregator-month-audit
description: Month-end deep audit of the aggregator channels (Talabat, Keeta, noon, Deliveroo, Careem). It checks every order, fee, cancellation, refund and payout against the marketplaces' own live data, attributes fault, fixes data gaps and code bugs, and reports the aggregator P&L. Use it on the 1st of each month for the previous month, or whenever someone asks to audit or reconcile aggregator sales, commissions, cancellations, disputes or refunds.
---

# Aggregator month-end audit

The aim is that every aggregator order for the month matches what the marketplace itself says, to the fil. You also report **why** anything that doesn't match is off (still waiting on the platform, or a bug), fix what can be fixed, and say what the month made.

This audit has been run by hand twice (Sept 2026 and Oct 2026). The patterns below come from those runs, so read them before concluding that something is a new bug.

The argument is the month as `YYYY-MM`. On the 1st with no argument, audit the previous month.

## 0. Access

All checks run on the prod VM, inside the live **api** container. The marketplace sessions live there, and httpx fetches from inside it look like the nightly sweep. Before you start:

- **Local machine.** Check the container name first with `docker ps --format '{{.Names}}'`; in Oct 2026 it was `melting-moments-cakes-api-1`. The deploy is blue-green, so it may be `-api-green-1` or `-api-blue-1` instead.
  ```bash
  gcloud compute ssh mm-backend --project=melting-moments-cakes --zone=me-central1-a \
    --command="docker exec -w /app <api-container> python -m scripts.aggregator_month_audit --month YYYY-MM"
  ```
  For SQL, use `docker exec -i melting-moments-cakes-postgres-1 psql -U mm_user -d mm_ecommerce`. Wrap reads in `BEGIN READ ONLY; … ROLLBACK;`.
- **Claude cloud session.** Use the `mm`, `mmsql` and `mmlogs` helpers (CLAUDE.md W11), never `gcloud compute ssh`.

Rules:
- Never run anything with `docker exec -d`; it dies silently. Run in the foreground and stream JSON to stdout. Files written to the container's `/tmp` can disappear.
- The VM is tiny (about 2 GB). Run one heavy fetch at a time, and never during a deploy cutover.
- Never start a headed Chrome while the aggregator-worker daemon is active.

## 1. Run the audit script

`apps/api/scripts/aggregator_month_audit.py` is read-only. It prints one `JSON{…}` line with the sections below.

| Section | What it checks |
|---|---|
| `coverage` | Per channel, how many delivered orders have fees, the GMV still waiting on fees, and the gap days already past the channel's settle lag. Gap days are the nightly catch-up's own test, `ingest.fee_gap_dates`. |
| `live_talabat` | Every order from Talabat `ListOrders` against our `net_payable`. |
| `live_careem` | Careem `billingEntries` fees against ours, plus the unpaid balance. |
| `live_deliveroo` | The daily order lists (order set, status, gross) and the weekly statements (net per order, plus period lines). |
| `live_noon` | The weekly statements (net per order, plus period lines). |
| `keeta_bills` | The ingested Keeta bills against the Keeta order feed. Keeta can't be fetched over httpx, so its bills are the portal truth. |
| `cancellations` | Every cancelled or refunded order, with its fault, reason, net and whether it is settled. |
| `payouts` | The last payout per channel, and Talabat's invoices tied to the sum of their orders' nets. |
| `pnl` | The `/profit-loss` figures for the window, from `pnl_report.build`. |

Save the JSON in the scratchpad and analyse it with Python. A section that errors is still a finding: say which one failed and why.

## 2. Expected versus a bug

### Fee lag (not a bug while inside the lag)

`ingest._FEE_SETTLE_DAYS` holds each channel's settle lag. The nightly fee catch-up only acts on orders past it.

| Channel | When fees land | Lag |
|---|---|---|
| Talabat | Billed the next day through the finance GraphQL. The sales sweep's enrichment picks it up. | 2 days |
| Careem | `billingEntries`, the next day. | 2 days |
| noon | Weekly RMS statement (Wed→Wed), published about a day after the week closes. | 9 days |
| Deliveroo | Weekly statement (Mon→Sun), paid the following Tuesday. | 10 days |
| Keeta | Fees are in the order feed from day 0. Bills arrive per cycle (1–7, 8–14, 15–21, 22–end of month), and only for ranges someone requested in the portal. | — |

On the 1st, the last week of the month will be pending for noon and Deliveroo, and the last day for Talabat and Careem. Report it as pending and say when it lands. **Coverage % = delivered orders with `aggregator_fee` ÷ delivered orders.**

### Patterns already found

Check against these before calling something new. Each has been fixed once, so a recurrence means a regression.

- **Fee-less days past the lag.** Causes: the nightly finance pass skipped a night, or Talabat's finance API returned 403 and enrichment silently skipped. Since PR "October audit", `ingest.sweep_fee_gaps_once` re-runs these every night. If they still appear, check that sweep's logs: `aggregator <ch>: N day(s) with unbilled sold orders`.
- **Talabat "Vendor refunds" on a delivered order.** This is money Talabat pays *us*: gross = subtotal + vendor refunds, and there is no refund. "Amount owed back to Talabat" on a delivered order is only the shortfall of a negative payout.
- **Talabat rider-fault cancellations** pay us 30% of the subtotal, shown as "Vendor refunds" and `billingStatus` PARTIALLY_REFUNDED. Vendor-fault cancellations claw back the commission (30% + VAT). Customer and platform cancellations are not charged.
- **noon `delivered_at` on a cancelled order** must be null. If it isn't, every noon cancellation reads as "Refunded after delivery".
- **noon charges commission + 2% payment fee + 2% cancellation fee** only on outlet-caused cancellations. If GrubOps says "Cancelled by Noon" but noon charged the outlet, list it as a **dispute candidate**.
- **Keeta partial refunds.** The feed sets `partRefundTag=1` and the bill gross is below ours. Once the bill lands, the shortfall is booked as `refund_amount` and the bill's commission wins.
- **Keeta desk cancellations (`canceledScene` 5050).** `responsible` 50/60 means Keeta pays. 20 is usually 0 or −bank fee, but was paid once (+48.34). Provisional nets wait for the bill.
- **Deliveroo restaurant-fault cancellations.** These appear as a `cancelled_order_charge` adjustment ("Party at fault: Restaurant"). The monthly platform fee is AED 200 including VAT, on the next month's first statement.
- **Careem** has no refunds/complaints feed (billingEntries is FOOD_ORDER only). Say it was **not audited** and suggest a manual check of the portal's Refunds & complaints page.
- **Talabat invoices.** These tie to order nets to within ±0.20 per period. A larger gap is usually an unnamed "Order Compensation" (`customer_refund` summary line). The pipeline already books it as a period refund.
- **Talabat payout cadence** is every 3–4 days, per chain. If the last payout is older than 6 days, flag it as outstanding money and give the amount (Σ order net since the last invoiced period).

## 3. Fix

### Data gaps

Use the normal idempotent path, the same one the nightly sweep uses. Run it in the api container, one channel at a time:

```python
import asyncio; from datetime import date
from app.services.aggregators import ingest
# Talabat fees: re-run the sales sweep day by day (the detail fetch is capped at 200 orders per run)
asyncio.run(ingest.run_range(["talabat"], date(Y,M,D), date(Y,M,D), modes=(ingest.RUN_MODE_SALES,)))
# Careem / noon / Deliveroo: finance
asyncio.run(ingest.run_range(["careem"], date(Y,M,D1), date(Y,M,D2), modes=(ingest.RUN_MODE_FINANCE,)))
```

Re-run the script afterwards and show that the diff is gone. The proof is "N/N orders now match".

### Code bugs

Work in an isolated worktree off a freshly fetched `origin/main`, because the shared checkout can switch under you. Follow CLAUDE.md:

- Fix the root cause in the provider or ingest code, with a comment naming the prod order that proved it.
- Add unit tests. For ingest and backfill changes, also add DB-gated integration tests, run against a local Postgres (`/opt/homebrew/bin/initdb` on port 5433, see `tests/integration`).
- Correct the data already wrong with a **guarded migration**: match the exact wrong value so it is a no-op once fixed.
- Validate the migration's SQL on prod inside `BEGIN; SET LOCAL lock_timeout='3s'; … ROLLBACK;` before shipping.
- Before committing, run `ruff check`, `ruff format`, and the unit suite.
- Commit author `Hussain Abbasi <h_abbasi97@hotmail.com>`, with no Co-Authored-By trailer.
- Push as the GitHub account `hussu97` (`gh auth switch --user hussu97`, then switch back to `h-abbasi`). The pre-push hook runs the full suite.
- Open a PR, wait for checks, and merge it by hand (auto-merge is off). A merge to main deploys.
- After the deploy, re-run the affected backfill if the fix needs re-parsing, then re-run the script to confirm.

### Things code can't fix

Put these in the report as actions for the owner: disputes to raise (with order ids and amounts), late payouts, Keeta bill cycles that need requesting in the portal, and staff process issues. One process issue seen before: Talabat cancellations made from the tablet with customer-side reasons ("bad location", "customer left") are charged to the restaurant.

## 4. Report

Lead with the answer, keep it compact, and don't narrate. Include:

1. **Tie-out.** Per channel: N/N orders, fees matching the portal to the fil, and what doesn't match and why.
2. **Fixed today**, with the backfills run and the PR link.
3. **Commission coverage %.** Per channel: how many orders are pending, why, and when each pending group lands.
4. **Cancellations and refunds by fault.** Restaurant, platform (rider or desk), customer, and the cost of each. Then dispute candidates.
5. **Payouts.** Anything late or outstanding.
6. **Aggregator P&L in a nutshell.** Per channel: GMV, take %, COGS %, PC3 and PC4. Then the expected movement once the pending fees and the month-end period charges land. Period charges to expect: noon's platform fee of 156.45 plus long distance, Deliveroo's platform fee of 200, and Keeta's last-cycle bill.

Finally, update the memory file for that month's audit, following the pattern of the earlier `mm-oct-aggregator-audit-2026-10-30` memory.
