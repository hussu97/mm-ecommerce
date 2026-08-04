# Melting Moments Ecommerce - Lessons Learned

> Updated as mistakes are corrected. Review at session start.

## Project Context
- UAE-based artisanal bakery (Melting Moments Cakes)
- Turborepo monorepo: Next.js 15 storefront + admin, FastAPI backend, PostgreSQL
- Design: dusty mauve (#8a5a64), Playfair Display + Jost, mobile-first, clean minimal
- ~33 SKUs across 5 categories, delivery across UAE

## Lessons

### [2026-08-04] Check the branch is current before designing on top of it
- **What went wrong**: six commits of courier and POS work were built on a `main` that was 26 commits behind `origin/main`. The remote had since added dynamic delivery pricing, a `pricing_mode` flag on `delivery_polygons`, a free-delivery scope change and a batch-retry path — none of it visible locally. The result is three colliding migration numbers, two Alembic heads at `056`, and a delivery model that has diverged in both directions.
- **Why it hid**: everything built, every test passed, and the local history looked linear. `git log origin/main..HEAD` reads "6 ahead" whether or not you are also behind; only the `[behind N]` in `git status -sb` says the other half.
- **Rule**: `git fetch && git status -sb` before starting any change that adds a migration or touches a shared model, and again before writing the plan. A plan written against a stale base is wrong in ways no test can catch.

### [2026-08-04] "Not found" from the wrong endpoint is not evidence of absence
- **What went wrong**: I checked noon Send's staging outlet codes with `GET /public/v1/pickup-points/{code}`, got "Pickup point not found" for all three, cross-checked against a `/public/v1/outlets` pull that did not contain them either, and told the user the outlets were not provisioned for our key. They were fine. `/pickup-points/{code}` only serves points a *partner* created (`addr::partner_pickup_point`); these are marketplace outlets (`addr::restaurant_outlet`), a different registry entirely. And the `/outlets` snapshot I pulled held 343 partner points and 165 nownow points and **zero** restaurant outlets — a partial response from an endpoint that started returning 503 minutes later.
- **Why it was convincing**: two independent-looking checks agreed. They were not independent — both were blind to the same registry, and one of them was a degraded response I never sanity-checked for completeness.
- **Rule**: before reporting that something a vendor told us about does not exist, confirm the endpoint you are asking is the one that would know, and check the shape of the answer for signs it is partial — a list with none of a category you expect is a suspicious list. When a user contradicts a finding with a screenshot of the live response, their evidence outranks a single sample of a flaky endpoint. Say so plainly and move on.

### [2026-08-04] A vendor's own timestamp is a claim, not a fact — check it against a clock you trust
- **What went wrong**: Lalamove's webhook `data.updatedAt` is Gulf local time carrying a `Z`, and its format is `HH:MM.ss`, which is not a time. We stored it verbatim, so `picked_up_at` and `delivered_at` were four hours in the future on every order ever delivered. Their own docs say "all timestamp information in Lalamove API are presented in UTC".
- **Why it hid**: four hours is not absurd. A delivery stamped 21:38 instead of 17:38 reads as a late-evening drop, and nothing in the row contradicts it. It was only visible because the same payload carries an epoch `timestamp` that disagreed — and that epoch matched our own received-at time to the second, on both orders independently.
- **Worse**: the string parses only by accident. Python 3.12 accepts `HH:MM.ss`, 3.14 raises. A runtime upgrade would have silently changed every recorded delivery time and disarmed the out-of-order guard, with no test failing either way.
- **Rule**: when a payload offers both an epoch and a formatted string, take the epoch. When it offers only a string, assert it against something independent — our own `now()` at receipt, or a second field — before trusting it in a column. And never let a `datetime.fromisoformat` on a third party's string be the only thing standing between a webhook and a timestamp column: pin the expected format in a test using a real captured payload.

### [2026-08-04] Do not hang behaviour off an event you have never received
- **What went wrong**: the "a rider is on the way" push to the POS fired only inside the `DRIVER_ASSIGNED` branch of the Lalamove webhook. Production has never received a single `DRIVER_ASSIGNED` — every webhook has been `ORDER_CREATED`, `ORDER_STATUS_CHANGED`, `POD_STATUS_CHANGED` or `WALLET_BALANCE_CHANGED` — so the counter was never told, and `driver_name`, `driver_phone` and `driver_plate` were empty on every delivery while `driver_id` was populated.
- **Why it hid**: the code is correct for the event it describes. Unit tests construct that event and it works perfectly. Nothing tests that the event ever arrives.
- **Rule**: after any integration goes live, `SELECT event_type, count(*) FROM webhook_events GROUP BY 1` and compare it against the event types the code branches on. A branch with no matching rows is either dead or waiting on a portal setting, and both are worth knowing before a customer does. Prefer hanging behaviour off state you can see changing (a `driverId` appearing) over an event you were promised.

### [2026-08-04] Copying a row means copying every column, not the ones you were thinking about
- **What went wrong**: the admin's copy-map-to-draft flow copied name, fee, pricing mode, free-delivery flag, geometry, bbox, display order and the batch windows — and silently dropped `branch_id`, a column added in the same session. Publishing such a draft would have pointed every zone at no kitchen, so every website order would have been written correctly and reached no register at all.
- **Why it hid**: the copy is a constructor call with eleven keyword arguments. A twelfth being absent looks exactly like the eleven being present, and no test asserted on a column that had been nullable-with-a-fallback for its whole life.
- **Rule**: when adding a column to a model that is copied anywhere — drafts, clones, duplicates, imports — grep for the model's constructor and fix every call site in the same commit. Then assert the copy round-trips the new column, because "it falls back" is exactly what stops the omission from being visible.

### [2026-08-04] Read the lessons file before repeating what is in it
- **What went wrong**: `057_noon_send_zone` published a fresh map version and seeded no `delivery_batch_windows` — the exact failure already written down two days earlier, in this file, as its own lesson. Every Lalamove order on the new map would have dispatched alone at roughly three times the per-delivery cost, silently.
- **Also**: those three lessons had been deleted from this file somewhere in the same run of commits, which is presumably why they were not read. The deletion went unnoticed until a line count in `git diff --stat` looked wrong.
- **Rule**: read `tasks/lessons.md` at the start of any delivery-map or batching work, and treat an unexplained deletion in `git diff --stat` as a bug to chase rather than noise. A `-14` on a file nobody edited is a fact, not a rounding error.

### [2026-08-03] Publishing a delivery map version must publish its schedule too
- **What went wrong**: `052` seeded batch windows against the polygons that existed when it ran. `055` then republished the entire map as fresh polygon rows and seeded nothing, so the live zones have carried no batch windows since and every order has been dispatched on its own — at roughly three times the per-delivery cost, with nothing on any screen saying so.
- **Why it hid**: a zone with no windows is not an error state. `find_window` returns `None`, `assign_or_dispatch` falls through to the single-order path, and every order still gets delivered. The failure is purely economic and completely silent.
- **Rule**: any migration or endpoint that creates `delivery_polygons` rows must create their `delivery_batch_windows` in the same transaction. The admin copy-to-draft flow already does this; migrations must too. When reviewing a new map version, query `(SELECT count(*) FROM delivery_batch_windows WHERE polygon_id = p.id)` per polygon before calling it done.

### [2026-08-03] Name a zone by the property that drives the behaviour, not by its label
- **What happened**: asked for batch schedules "for dubai, sharjah, ajman city", the user had to clarify that they meant the polygons with static fees rather than the three specifically-named ones.
- **Rule**: when a rule splits zones, express and confirm the split by the property that decides it (`pricing_mode`, `fulfilment_provider`) rather than by the zone names that happen to hold that property today. The names change when the map is redrawn; the property is what the code branches on.

### [2026-08-03] A quote that becomes a price needs different timeouts and caching
- **What went wrong nearly**: `estimate_for_point` capped the checkout quote at 4s and cached failures for 120s, both correct while the quote was only margin data nobody saw. The moment the quote became the fee outside the fixed-price zones, a 4s cap meant a real address being told "we don't deliver here", and a 120s failure cache meant it stayed told for two minutes after the courier recovered.
- **Rule**: when a value moves from "recorded" to "displayed and charged", re-read every timeout, retry and cache TTL around it. The settings that were cheap insurance for a background number become customer-visible failures for a foreground one.


### [2026-08-03] Treat each operational branch pin as an independently verified record
- **What happened**: Correcting the Sharjah kitchen exposed a separate, substantially incorrect Barsha Heights branch longitude.
- **Rule**: Resolve every owner-provided Google Maps place link and update the matching branch record independently; never infer one branch's coordinates or address from another branch or from a map viewport.

### [2026-08-03] Confirm a customer-provided Maps place before changing delivery coordinates
- **What happened**: The user questioned the Melting Moments Cakes delivery pin after seeing checkout behaviour, despite the selected Google place containing a specific Maps short link.
- **Rule**: Resolve the exact Google Maps place URL, compare its canonical latitude/longitude with the saved checkout and Lalamove payload values, and use the owner-provided address text verbatim when it is more operationally precise than Google's formatted address.

### [2026-08-03] Maps JS key checks must preserve the browser referrer error
- **What went wrong**: A command-line bootstrap probe was summarized as a generic Maps authentication failure, although the browser supplied the actionable `RefererNotAllowedMapError` for the checkout URL.
- **Rule**: When diagnosing a browser-restricted Google Maps key, inspect the browser console error first and validate the exact production origin/path restriction. Do not collapse it into a generic key-invalid conclusion.

### [2026-04-13] New env vars must be added to all 4 places
- **What went wrong**: Added `GCP_PROJECT_ID` to `docker-compose.prod.yml` and `.env.example` but missed the GitHub Actions `deploy.yml` and `rollback.yml` — the secret would never reach the VM's `.env` file
- **Why**: The `.env` on the VM is written entirely by the CI `printf` block; any secret not listed there is silently absent at runtime
- **Rule**: Any new env var/secret must be added to ALL four locations simultaneously:
  1. `apps/api/.env.example` — with a comment
  2. `PRODUCTION.md` Step 13c — in the secrets table
  3. `.github/workflows/deploy.yml` — in the `printf` block ("Write .env on VM")
  4. `.github/workflows/rollback.yml` — same `printf` block (must stay in sync)

### [2026-04-14] i18n seed runs automatically on API startup — no manual step needed
- **What happened**: Added a new `product.out_of_stock` translation key and initially noted it required a manual `python -m scripts.seed_i18n` run
- **Reality**: `main.py` lifespan hook imports `seed_i18n` and runs it on every startup (non-fatally). New keys land in the DB automatically on next deploy/restart
- **Rule**: Never tell the user to manually run `seed_i18n`. Any new translation key added to the seed script will be picked up automatically. Check `main.py` lifespan before assuming manual intervention is needed for seed scripts

### [2026-04-14] Stock-product items with stock_quantity=0 must be blocked at every layer
- **What went wrong**: Gift note card had `is_stock_product=True`, `stock_quantity=0`. The `add_item` endpoint only checked `is_active`, so the item could be added to cart. During guest→user cart merge, `merged_qty` was capped to 0, creating a `quantity=0` cart item → `line_total = price × 0 = 0 AED` in checkout
- **Rule**: Enforce stock at three layers:
  1. **API `add_item`**: Reject with 400 if `is_stock_product=True` and `stock_quantity <= 0`
  2. **API merge**: Skip (continue) items where stock cap reduces `merged_qty` to 0 — never persist a zero-quantity cart item
  3. **Frontend**: Show disabled "Out of Stock" button when `is_stock_product=True` and `stock_quantity <= 0`; expose `stock_quantity` in `ProductResponse` schema so the frontend can act on it

### [2026-06-05] Admin product "Order" is display_order, not stock_quantity
- **What went wrong**: Interpreted the admin product edit screen's `Order` input (`10000` for Gift Note Card) as stock quantity during an OOS investigation.
- **Reality**: `ProductForm.tsx` maps `Order` to `display_order`; the admin form exposes `Track Stock` but does not expose a `stock_quantity` input.
- **Rule**: When investigating stock status, verify `stock_quantity` from the API/DB or an explicit stock field. Do not infer inventory from the admin `Order` control.

### [2026-06-06] Never commit admin passwords or password hashes
- **What went wrong**: Documented an admin bootstrap password in task notes and added password hashes to migrations.
- **Reality**: Passwords and password hashes are credentials. Even if production needs a direct credential update, it must be performed out-of-band and kept out of git, docs, migrations, logs, and final summaries.
- **Rule**: When adding admin users, implement a proper onboarding/reset flow or perform a one-time DB update without committing the password or hash anywhere in the repo.

### [2026-03-05] Python: Don't use passlib with Python 3.14+
- **What went wrong**: `passlib[bcrypt]` crashes on Python 3.14 — `bcrypt.__about__` attribute was removed in bcrypt 4.x, causing passlib's backend detection to fail
- **Why**: passlib is largely unmaintained; it hasn't caught up with newer bcrypt API changes
- **Rule**: Use `bcrypt` directly instead of `passlib`. Pattern:
  ```python
  import bcrypt
  def hash_password(pw: str) -> str:
      return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
  def verify_password(plain: str, hashed: str) -> bool:
      return bcrypt.checkpw(plain.encode(), hashed.encode())
  ```
- **pyproject.toml**: Use `"bcrypt>=4.0.0"` — remove `"passlib[bcrypt]"`
