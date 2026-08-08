# Lalamove for third-party zones, and an order history that can date itself

## Why

Two asks that turned out to share one root cause: **an order with no integrated
courier produces no facts, so nothing downstream has anything to show.**

1. A zone marked `third_party` has no integration by design — someone we already
   use collects the box and tells us nothing. That is fine until it is not, and
   there was no way to put one of those orders on a courier we can actually
   book.
2. The admin's status timeline showed a stamp under *Created* and four blanks.

The timeline was blank because four of its five stamps read `order_deliveries`
columns that only a courier webhook ever fills — so a pickup order, a
third-party zone, or anything walked through by hand filled in exactly one.
`confirmed` had no source at all.

## What was already there

Lalamove is fully integrated: HMAC client, quote/dispatch/webhook/POD/driver,
batching, a signed receiver, and `LALAMOVE_*` in all five secret locations.
**No new env vars**, so CLAUDE.md §9 does not apply and
`test_compose_env_allowlist.py` is untouched.

Three early-returns kept third-party orders away from it, and all three key off
one column — `OrderDelivery.provider`. Changing it is what makes the existing
machine take the order.

## Done

### 1. `order_status_events` — one source of truth for the journey
- [x] New table: status, previous_status, at, source, actor, note
- [x] Captured by a **SQLAlchemy listener on `Order.status`**, not by callers.
      `Order.status` is assigned from 13 places and only 2 go through
      `order_service.update_status`; a recorder each writer must remember to
      call is already incomplete
- [x] Attribution from a `ContextVar` set at the entry points that know who is
      acting — admin endpoint, register, payment webhooks, both couriers,
      checkout. Unset means `system`, which is honest rather than wrong
- [x] Courier webhooks pass `at=` so the history carries **their** moment, not
      our processing time
- [x] Migration `091` backfills from `created_at`, `order_deliveries`,
      `audit_logs` and `payment_transactions`; never from `updated_at`
- [x] **Dropped `order_deliveries.picked_up_at` / `delivered_at`** — they were
      the same two moments, in a second table, read by a different screen.
      `booked_at`, `cancelled_at` and `dispatchable_at` stay: none is an order
      status
- [x] `status_history` on `OrderResponse` (timestamps only) and
      `GET /orders/{n}/status-events` for the admin (with actor and source)

### 2. The estimate stays date-based
- [x] `promised_precision == "day"` is now a **ceiling for the life of the
      order**. Every branch of `_estimate` used to key its sharpness off the
      *current* provider, so a reassignment turned "Sat 9 Aug, before 10 PM"
      into "Sat 9 Aug, 18:40" the moment a rider collected
- [x] `original_provider` on `order_deliveries` so an order with no stored
      promise still remembers it was somebody else's van
- [x] Storefront `formatMoment` gained the missing `day_by` case — it had been
      rendering an appointment where the email and admin said "before 10 PM"
- [x] `order.estimate_by_time` seeded EN + AR

### 3. Quote → confirm → book
- [x] `POST /orders/{n}/delivery/lalamove/quote` — prices the order's own
      address via the same `resolve_pickup` + `build_drop` the booking uses, not
      the two-minute-cached `estimate_for_point`
- [x] `POST /orders/{n}/delivery/lalamove/assign` — books at the approved
      quotation. Guards: status `packed`, provider `third_party`, no existing
      booking, courier configured
- [x] A lapsed quotation is a **409 carrying the new price**, never a silent
      re-book
- [x] Failure puts `provider` back to `third_party` — a row saying `lalamove`
      with no booking is the one state that strands a paid, packed order
- [x] **The customer's fee never changes.** The quote is our cost; the margin is
      what the admin is accepting

### 4. Things the audit found on the way
- [x] `ORDER_REPLACED` was unhandled. Lalamove cancel-and-clones a matched order
      to adjust it, so the row was left saying "nobody is coming" while the
      parcel was on its way — and re-dispatching would have put a **second
      driver on the same cake**
- [x] The provider only parsed `{"errors": [...]}`, but the quotation endpoint
      answers `{"message": "ERR_..."}`. Every `error_id` predicate was silently
      false there: checkout said "quote failed" for an out-of-range address, and
      `batching_service` retried dispatches that could never succeed
- [x] `ApiError` flattened FastAPI's structured `detail` to `[object Object]`

### 5. Admin
- [x] Timeline reads `status_history` — five stamps on every order
- [x] "Assign to Lalamove" on a packed third-party order, with a dialog showing
      cost, distance, what the customer paid and the margin (red when negative)
- [x] "moved from Third party" on the delivery card

## Verification

- 1257 passing, 21 skipped; `ruff check` and `ruff format --check` clean
- Both Next apps build; both typecheck
- Migration `091` applied, downgraded and re-applied on a **throwaway Postgres**
  with seeded fixtures covering a third-party delivered order, a Lalamove order
  with full telemetry, an admin-walked order and a card order. Backfill verified
  row by row, including the duplicate audit row deduping to the earliest
- Downgrade refills `picked_up_at` / `delivered_at` from the history, so going
  back loses nothing
- The listener verified live against the real schema across every writer style:
  constructor, direct assignment, `acting_as` block, and a raw string

## Still open

- Not run against Lalamove **sandbox** end to end. The unit tests mock `httpx`,
  so the request shapes are pinned but a real quotation has not been bought.
