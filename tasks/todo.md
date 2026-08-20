# Driver reassignment, the driver slip, and how far away they are

## The ask

1. **Driver reassignment** — when a courier swaps riders mid-booking, update the
   driver on our side and show the latest everywhere it is shown. Both couriers
   have a webhook.
2. **A driver slip on the POS** — once a driver is assigned, print a small slip
   carrying the same order reference as the original receipt, the driver's name
   and their number. Reprint it whenever the driver is reassigned.
3. **Distance from the branch** — show roughly how far the driver is from the
   branch, on the register beside the order and on the admin order page beside
   the driver details and the live tracking URL.

## What was already there (and what was not)

`order_deliveries` had one set of driver columns — id, name, phone, plate,
latitude, longitude — and no memory of any other driver.

The two services filled them only when the row had **none**:

| Courier | The guard | Why a swap slipped through |
|---|---|---|
| Lalamove | `if delivery.driver_id and not had_driver` | true exactly once per booking |
| noon Send | `if status == assigned and status != courier_status` | a swap re-sends `assigned`, so the transition test is false |

So the first rider was recorded perfectly and every one after them was thrown
away. The row kept a name and a number belonging to somebody who had dropped the
job; the admin card said the same; the counter rang them.

Positions were worse. noon Send pushes one every 15-30 seconds and nothing read
the name beside it. Lalamove pushes one **exactly once**, inside the driver
detail fetched when the id first appears — so by pickup it was twenty minutes
old, and there was no stamp saying so.

## What was built

### Data — `order_drivers`, one row per stint

Keyed to the **order** rather than the booking: a booking is not stable
(`ORDER_REPLACED` clones one under a new id; a re-dispatch makes another), and
the driver who collected order 4 collected order 4 whichever id was live.

`is_active` is `TRUE` on the current stint and **NULL** on every past one, under
`UNIQUE (order_id, is_active)`. Postgres counts NULLs in a unique index as
distinct, so any number of finished stints coexist while a second live one is
refused by the database. A CHECK keeps `FALSE` out — a `FALSE` row would collide
with every other finished row.

**The uniqueness is `(order_id, is_active)`, not `(order_id, driver, is_active)`.**
With the driver in the key, two *different* people could both be active on one
order, which is the exact thing the constraint exists to prevent; what it would
buy instead — refusing the same person twice — is not a real failure mode, and a
driver genuinely handed an order back is a second stint that deserves its own row.

`order_deliveries` keeps its driver columns as a live copy of the active row.
One function writes both in one transaction, so they cannot drift, and the hot
readers (the register's order list, the 15-second tracking webhook) need no join
to say who is coming. Same arrangement `orders.status` has with
`order_status_events`. Three columns added there: `driver_assigned_at`,
`driver_assignment_count`, `driver_location_at`.

### One decision, both couriers — `services/driver_assignment.py`

`record(db, delivery, driver)` returns `ASSIGNED`, `REASSIGNED` or `UNCHANGED`,
and is the only writer of a driver anywhere. Identity is compared on the
strongest handle both sides carry — id, then phone, then name — because
noon Send's `da_details` has no id at all and a rider swap there is only ever
visible as the name or the number changing.

Everything else keys off the answer: the push wording, the reprint, the admin
badge. There is no second code path for reassignment and no flag to forget.

### Learning about a swap at all

* **noon Send** — the task detail is now re-read on every pre-collection status
  push (`assigned`, `arrived_at_pickup_location`) rather than only on the
  *transition* to `assigned`; and the tracking webhook, which fires every 15-30
  seconds and carries a name and a number nobody was reading, now folds them in.
* **Lalamove** — their documented `DRIVER_ASSIGNED` event has never once arrived
  in production, and a swap does not change the status, so **there is no push at
  all**. `services/driver_tracking.py` reads the booking back on the minute,
  inside the existing `batch_scheduler` sweep and its advisory lock. It is
  bounded: only bookings with a matched driver who has not yet collected,
  stalest position first, capped per tick.

### The routed distance — Mapbox Directions

The first cut answered with straight line × 1.49. That factor is fitted well
enough to price a zone on average and poorly for one rider: it does not know
about the creek, the one-ways round Al Majaz, or which bridge is between them
and the shop. A driver 2 km away as the crow flies can be 6 km of road.

`providers/mapbox_provider.py` calls `driving-traffic` with `exclude=toll`,
matching the call shape already proven in production in `fastfishio/sc-food-api`
(a different account — MM has its own `pk.` token). `services/driver_routing.py`
refreshes the cached route on the sweep's minute rather than per position push:
noon Send pushes every 15-30 s, and calling from there would both waste calls on
an answer that changes once a minute and put a third party's latency inside a
webhook noon retries.

Both numbers cache on the delivery. `driver_route_at` is stamped when the
**route** was computed, not when the position was — the two go stale for
different reasons (a rider moves; separately Mapbox stops answering), and a
fresh pin with an old route has to degrade rather than quote minutes for a road
the driver left. A failed call deliberately does **not** stamp, so a broken
token cannot look fresh for a minute at a time and suppress its own retry.

**The fallback returns no minutes at all.** A duration got by dividing a
straight-line estimate by an assumed speed is a guess wearing the clothes of a
measurement, and the counter cannot tell the two apart. Its absence is also what
both screens use to say "estimate" rather than claiming a route.

`MAPBOX_ACCESS_TOKEN` is optional and went through all five locations in
CLAUDE.md §9 — `test_compose_env_allowlist` failed on the first run for exactly
the reason it exists, which is the check working.

### The distance — `services/driver_proximity.py`

Computed server-side and rendered by both clients, for the same reason money is:
two screens deriving a kilometre from raw coordinates would eventually disagree
about the same driver.

It declines to answer far more often than it answers, and each refusal is the
point:

* no position, or one with no `driver_location_at` — an undated pin reads
  identically whether it was true twenty seconds or twenty minutes ago;
* a position past `MAX_AGE` (6 min) — a dead feed goes quiet rather than lying;
* the parcel is already collected — from then on the driver is *supposed* to be
  getting further away.

`STALE_AFTER` (75 s) < `MAX_AGE` (6 min) is asserted in a test, so the refresh
window and the quoting window cannot drift apart and blink the number out.

### The slip — `MMPos/Features/Incoming/DriverSlipModel.swift`

The receipt prints at accept, minutes before any courier has matched anybody, so
it cannot name a driver. The slip is that name on paper, carrying **the same
number as the receipt from the same function** — the courier reference where
there is one, ours otherwise.

Two rules do the work:

* **Only the terminal that printed the receipt prints slips.** `watch` is called
  where the receipt is printed. Every terminal at a branch sees every online
  order, so a rule based on the order alone would put a slip on every printer in
  the shop.
* **A slip is owed when the server's `driver_assignment_count` exceeds the one
  this terminal last printed.** The reprint on reassignment falls out of that
  comparison — the same one that catches the first driver catches the fifth.

The ledger is persisted per terminal (an iPad dies on the charger mid-shift) and
written *before* the print, because `PrintService` retains a failed job for retry
and treating a failure as "not printed" would hand the counter two slips.

`PushService` grew a keyed observer list. It had a single closure, and the second
thing to attach would have silently replaced the first — a shop that stopped
hearing about orders the day slips were added.

## Verification

* API: **1735 passed, 21 skipped** (up from 1708 + 21); 27 tests added across
  three files. `ruff check` and `ruff format --check` clean. Alembic graph
  resolves to a single head at `113_driver_route`.
* Admin: `tsc --noEmit` clean, `eslint` clean, 41 vitest tests pass.
* `packages/types` regenerated from the OpenAPI document in the same change
  (CLAUDE.md §8).
* POS: `swift test` **282 tests, 0 failures** (up from 265); both app targets
  build for their simulators.
* **Not verified locally: the token itself.** It lives in GitHub secrets and
  never reaches this machine, so the first real proof that Mapbox answers is
  the API log after the deploy. `mapbox_provider` logs the status and the
  truncated body on any non-200, so a wrong or URL-restricted token says so by
  name rather than silently falling back.

## What was deliberately left out

* **No customer-facing distance.** The storefront is still not told which
  courier carries an order, let alone where they are.
* **No polling of noon Send for position.** They push every 15-30 seconds; a
  sweep would be paying for something already arriving.
* **No ETA to the *customer*.** The routed leg is driver→kitchen. Quoting an
  arrival time to somebody at home needs the second leg and a promise the shop
  has not agreed to make.
* **No distance after collection**, on either surface. The number exists to
  answer "how long until somebody collects this", and it stops meaning that the
  moment they have.
