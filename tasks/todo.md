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

---

# CI: cutting the workflows down (2026-08-08)

Measured first, from `gh api .../jobs`, rather than guessing:

| Workflow | Was | Critical path |
|---|---|---|
| PR Check | 241s | web job 228s, of which **Build 171s** |
| Deploy (web) | 351s | changes 7s → lint-web 48s → deploy-web 282s |
| Deploy (api) | 211s | changes 5s → lint-python 97s → deploy-gcp 94s |

## Done

- [x] **`optimize-images.mjs` ran on every build — 149s of the web app's 171s
      build step.** From the run log: `08:33:16 → 08:35:45` for the images, then
      ~22s for `next build`. It was paid twice per deploy, because `vercel build`
      runs the same npm script. `public/images` is committed, and running the
      optimiser locally changed **0 of the 45 files** — the work was pure waste
      on every run. Removed from `build` and `dev`; kept as `pnpm --filter web
      images`, with `check-images.mjs` in its place (0.038s) so a change to
      `image-src` without a re-encode fails the build instead of silently
      shipping the old artwork
- [x] `lint-web` / `lint-admin` / `lint-python` folded into the deploy jobs as
      steps. As jobs they gated the deploy serially, so every deploy booted a
      second runner and re-did checkout + setup-node + install to reproduce the
      machine the deploy was about to build on
- [x] `concurrency` groups. PR Check cancels superseded runs; **Deploy
      deliberately does not** — it runs migrations and restarts containers, and
      killing it partway is how you get a half-migrated database
- [x] `pip` → `uv` for the API deps (19s → ~2s)
- [x] Dropped `-v` from pytest, which printed 1280 lines nobody reads
- [ ] ~~`pytest -n auto --dist loadfile`~~ — **tried and reverted.** Measured
      50s against 47s serial on the runner: slightly *worse*. The suite's CI
      cost is importing the app, not running the tests, and every xdist worker
      pays that import separately. It does help locally (9.6s -> 5.6s), so
      `pytest-xdist` stays as a dev dependency

## Verification

- Guard proven **both** ways: passes on a clean tree in 0.038s; adding an
  un-encoded file to `image-src/logos` makes it exit 1 with the regenerate
  instruction; passes again once reverted
- Re-running the optimiser still produces byte-identical output — 0 changed files
- `pnpm --filter web build` works through the new guard; lint clean (12
  pre-existing warnings, 0 errors), 277 tests pass, `tsc` clean
- Suite checked against a **throwaway Postgres**, not just the mocked run:
  1281 passed, twice consecutively
- Measured on the runner afterwards, which is how the xdist regression was
  caught. Per-step, against a baseline run that also touched `deploy.yml` so
  the same three jobs ran in both:

  | Step | Was | Now |
  |---|---|---|
  | web `Build` (the image fix) | 167s | **30s** |
  | admin `Deploy` (`--archive=tgz`) | 165s | **18s** |
  | API `Install dependencies` (uv) | 19s | **2s** |
  | API `Run pytest` (xdist) | 47s | 50s — reverted |

  Full three-app deploy 316s -> 263s, and 63s of that 263 was a one-time Docker
  layer rebuild from `pyproject.toml` changing
- `actionlint` clean on both changed workflows

## Still open

- **PR Check's Python job has no Postgres service**, so the 21 real-database
  tests only ever run on the deploy. A PR can go green and then fail on `main`.
  Adding the service there costs ~22s and closes the gap
- The web `Build` and `Deploy` steps still repeat work between PR Check and the
  deploy. A Turbo remote cache would share it, but it needs a token and a
  service, so it was left alone

## Follow-up: the Vercel upload is retried now

Run 31260006904 failed with a bare `Error: fetch failed` 7.3MB into a 29.2MB
tarball upload. Not a quota and not a config fault — the identical command had
succeeded for admin two minutes earlier and for web twenty minutes before that,
and the build was already finished and paid for when the connection dropped.

Both Vercel `Deploy` steps now retry three times with a 10s/20s backoff. Safe to
retry: a deployment only goes live once its upload completes, so a half-sent
archive promotes nothing.

Steady state, measured on run 31260260610 once the API's Docker layer cache was
warm again (`pyproject.toml` changing had busted it on the two runs before):

| Job | Was (lint + deploy, serial) | Now (merged) |
|---|---|---|
| Web | 55 + 246 = 301s | **142s** |
| Admin | 42 + 71 = 113s | **104s** |
| API | 106 + 78 = 184s | **161s** |
| **Whole run** | **316s** | **173s** |

Per step, where it came from:

| Step | Was | Now |
|---|---|---|
| web `Build` — the image fix | 167s | **32s** |
| admin `Deploy` — `--archive=tgz` | 165s | **18s** |
| API `Install dependencies` — uv | 19s | **2s** |
| API `Build and push API image` | 4s | 10s (cache warm; 48–67s while busted) |
| API `Run pytest` | 47s | 49s serial — 50s under xdist, which is why it was reverted |

Beware comparing against the failed run 31260006904: its web job reads 104s only
because the job died early at `Deploy`. 142s is the real figure.
