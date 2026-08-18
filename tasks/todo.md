# Configurable delivery estimates + branch holidays

## The ask

1. noon Send's promise moves 60 → 90 minutes.
2. …and better: make the three delivery-estimate numbers configurable rather
   than deploy-gated — noon Send zones (minutes), Lalamove batches
   (minutes-to-door), third-party zones (days).
3. Branch holidays, configurable in the admin branches tab, whole-day only
   (the shop trades seven days a week; weekends are not holidays).
4. The delivery estimate reads them, so a promise never lands on a day the
   branch is shut.

## What is already there (and what is not)

`services/delivery_promise.py` is already the single resolver — four rules,
first match wins — and two of the three numbers already live in the database:

| Number | Where it lives today | Editable? |
|---|---|---|
| noon Send minutes | `couriers.unbatched_promise_minutes` | **no UI, no API** |
| Lalamove batch minutes-to-door | `delivery_batch_groups.delivery_minutes_after_dispatch` | **no UI, no API** |
| Third-party days | nowhere — `days = 1` hardcoded in rule 3 | **no** |

So this is mostly *exposure*, not new modelling: one new column, two new
write endpoints, one new screen. The only genuinely new concept is the
holiday.

## Design

### A. `branch_holidays` — the one new table

* `branch_id` → branches (CASCADE), `holiday_date` `String(10)` `YYYY-MM-DD`
  (matching how `business_date` is stored, with the same format CHECK),
  `name`, optional `note`.
* Unique on `(branch_id, holiday_date)`.
* **Whole days only, by design** — no hour columns. A half-day closure is a
  trading-hours change, which the branch already has fields for; giving
  holidays hours would be a second answer to the same question.
* Weekends are deliberately *not* derived. A closure is a row somebody wrote,
  never a weekday rule.

### B. `core/trading_hours.py` learns about closed days

The module is already the one definition of "is the kitchen open". Holidays
belong to it rather than to the promise resolver, so the dispatcher and the
promise cannot disagree later. Additive, keyword-only, defaulting to empty —
every existing call site keeps its behaviour:

* `trading_date(moment, opens, closes)` — which trading day an instant belongs
  to. A branch open 09:00–02:00 is still on yesterday's day at 01:00, so a
  holiday closes that tail too.
* `is_open(..., closed_dates=())` — false on a closed day.
* `next_opening(..., closed_dates=())` — skips closed days.
* `first_open_day(day, closed_dates)` — first date on/after `day` that trades.

### C. The resolver

`_Context` gains `closed_dates`; `_load` reads the holidays of the branch that
already resolves the trading hours (same branch, same fallback, one query).

* **Rule 2 (batch)** — re-match from the start of the next day while the run
  would leave on a closed day. Nothing is packed on a holiday, so a slot that
  day does not fire.
* **Rule 3 (next_day)** — `days` becomes `couriers.unbatched_promise_days`
  (new column, default 1, so today's behaviour is unchanged). The base day is
  the first day the kitchen can work on it, and the arrival is then pushed off
  a closed day too — a promise never names a day the shop is dark.
* **Rule 4 (minutes)** — falls out for free: `is_open` is false on a holiday,
  so it already takes the "start the clock at the next opening" branch, and
  that opening now skips holidays.

The dispatcher (`batching_service`) is deliberately untouched: an order only
becomes dispatchable when a human packs it, and nobody packs on a holiday.

### D. API

* `GET /delivery-zones/couriers`, `PUT /delivery-zones/couriers/{code}` —
  promise kind, minutes, days (`delivery.manage`).
* `PUT /delivery-zones/batch-groups/{id}` — minutes-to-door, active
  (`delivery.manage`).
* `GET/POST /branches/{id}/holidays`, `PUT/DELETE /branches/holidays/{id}`
  (`admin.branches.manage`), following the sections/tables shape already in
  that router.

### E. Admin

* **Delivery zones → new "Estimates" tab.** Both halves of "what do we tell
  the customer" on one screen: the courier promises, and each batch group's
  minutes-to-door.
* **Branches → "Holidays" section.** Branch picker + dated rows.

### F. The 60 → 90 change itself

A content edit, so `scripts/`, not a migration (CLAUDE.md §7). Ships as
`scripts/set_courier_promise.py`; after deploy it is also one field on the new
Estimates screen.

## Added mid-task

5. Order status may move **undelivered / cancelled → delivered**, from the admin
   console only, as a manual correction.

### Design

`undelivered` and `cancelled` are endings and `VALID_TRANSITIONS` keeps them
that way — that map is also read by the courier webhooks and the register, and
widening it would let a late `delivered` push resurrect an order the shop had
already written off and refunded.

The route is `extra_from`, which `order_lifecycle.transition` already has for
exactly this ("this one caller may leave a state the map closes"), driven by a
new `ADMIN_RECOVERABLE` map. `order_service.update_status` — the admin-shaped
doorway, and the only caller of it — is the only thing that passes it.

Two consequences follow, and one deliberately does not:

* **Stock.** Cancelling hands a website order's stock back; delivering after a
  cancellation has to take it off again, or every corrected cancellation
  overstates stock by its own contents, permanently and silently. The restock
  and this became one `_move_stock(db, order, ±1)`, and `_consequences` now
  receives the previous status so it can tell which.
* **The refund.** Not reversed — the money is at a bank, not in a column. It
  warns instead, and the admin confirm dialog says so before the click.
* **The register void.** Left alone: a settled check reopened is a till that no
  longer reconciles.

The admin order screen also had three buttons left behind by the earlier change
that made `undelivered` an ending — "Mark Delivered", "Return To Packed" and
"Cancel Order" on an undelivered order. The API refused all three, so they could
only produce a red toast. The first now works; the other two are gone.

## Checklist

- [x] Read the existing resolver, models, admin screens, tests
- [x] `models/branch.py`: `BranchHoliday`
- [x] `models/courier.py`: `unbatched_promise_days`
- [x] Migration `106` (schema only)
- [x] `core/trading_hours.py`: closed-day awareness
- [x] `services/delivery_promise.py`: the three rules
- [x] `services/branch_holiday_service.py`
- [x] API: couriers, batch-group update, branch holidays
- [x] Admin: Estimates tab, Holidays section, api bindings + types
- [x] Regenerate `packages/types` from the OpenAPI document
- [x] Tests: holiday cases per rule, trading_hours units, config plumbing
- [x] `scripts/set_courier_promise.py` + PRODUCTION note
- [x] `ADMIN_RECOVERABLE`: undelivered/cancelled → delivered, console only
- [x] Run the API suite and the admin typecheck
- [x] Commit and push to `main`

## Review (2026-08-18)

**One new concept, not four.** The three "configurable numbers" turned out to
be two existing columns with no way to write them plus one hardcoded `1`, so
the delivery half of this is a new column, two write endpoints and a screen —
the resolver's four rules are untouched in shape. The genuinely new thing is
the closed day, and it went into `core/trading_hours.py` rather than into the
promise resolver, so the dispatcher's `kitchen_is_open` and the customer's
promise still read one definition. Both are additive and default to empty, so
every existing call site behaves exactly as before.

**What the holiday rules came out as.** A promise never names a day the branch
is shut — that is the whole rule, applied three ways: a batch run re-matches
onto the next open day, a third-party day-promise walks its base day and its
arrival day off closures, and the minutes rule needed no new code at all
because `is_open` going false on a holiday already routes it through
`next_opening`. Rule 2's re-match is bounded at 30 days and falls through to
the courier rules rather than looping, so a year of holidays cannot hang a
checkout.

**The order correction reused a seam rather than widening the map.** `extra_from`
already existed for "this one caller may leave a state the map closes", so
`undelivered`/`cancelled` → `delivered` is open to the console and closed to
every webhook, with a test pinning both halves. The part worth having found is
the stock: cancelling gives it back, so correcting a cancellation has to take it
again, and the two are now one function read in both directions.

**Numbers, measured rather than assumed.** 1619 passing and 21 skipped in the
API suite (up from 1577 + 21), 44 tests added across five files; admin typecheck,
eslint and its 35 vitest tests clean; web typecheck clean; `packages/types`
regenerated and `check:fresh` green, with the OpenAPI path set gaining exactly
the five new routes and losing none. Migration 106 renders valid Postgres
offline and leaves a single alembic head.

The 60 → 90 change itself is **not applied by the deploy**: it ships as
`scripts/set_courier_promise.py` (content edit, CLAUDE.md §7), and is also one
field on the new Estimates screen.
