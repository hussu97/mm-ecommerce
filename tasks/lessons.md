# Melting Moments Ecommerce - Lessons Learned

> Updated as mistakes are corrected. Review at session start.

## Project Context
- UAE-based artisanal bakery (Melting Moments Cakes)
- Turborepo monorepo: Next.js 15 storefront + admin, FastAPI backend, PostgreSQL
- Design: dusty mauve (#8a5a64), Playfair Display + Jost, mobile-first, clean minimal
- ~33 SKUs across 5 categories, delivery across UAE

## Lessons

### [2026-08-08] A sentinel that is an `Enum` will pass an `isinstance(x, Enum)` check
- **What went wrong**: the status listener took `oldvalue` from SQLAlchemy's
  `set` event and normalised it with `if isinstance(status, Enum): return
  str(status.value)`. On a brand-new object there is no old value, and what
  SQLAlchemy hands back is `LoaderCallableStatus.NO_VALUE` — a member of *its*
  enum, whose `.value` is `4`. Every order's first history row came out with
  `previous_status = '4'`.
- **Why it hid**: `4` is a plausible-looking string in a nullable column nobody
  reads on the first row. It only surfaced because a live round-trip printed the
  transition chain and the first arrow said `4 -> created`.
- **Rule**: normalise against the *specific* types you expect (`OrderStatusEnum`,
  `str`), never against a structural supertype. A library's "there is nothing
  here" sentinel is an object like any other, and a duck-typed check will happily
  serialise it into a column. And run one real round-trip printing the values —
  a type check that accepts the wrong thing cannot fail a test that only asserts
  the right thing is present.

### [2026-08-08] Two tables holding one fact are two answers to one question
- **What went wrong nearly**: `order_status_events` was added to give the admin
  timeline its missing stamps, alongside `order_deliveries.picked_up_at` and
  `delivered_at` — which are the same two moments, written by the same webhook a
  few lines apart. Hussain caught it before it shipped: the customer's timeline
  would have read one copy and the admin's delivery card the other, free to
  drift, with no error anywhere when they did.
- **The subtlety that made the fix bigger than a delete**: the columns held the
  *courier's* stamp, and the new rows were being written with `utcnow()`. Moving
  the source without carrying the moment across would have silently redefined
  "picked up at" as "webhook processed at" — which for Lalamove, who retry an
  unacknowledged event for a day, is a different answer entirely.
- **Rule**: when adding a table that records something an existing column
  already records, delete the column in the same change or do not add the table.
  And before deleting it, check what the old writer knew that the new one does
  not — a timestamp's *provenance* travels with the value, and a migration that
  moves the number without the provenance moves a different number.

### [2026-08-08] A predicate that is never true reads exactly like a condition that never happens
- **What went wrong**: `LalamoveError.is_out_of_service_area` had never fired.
  The response parser only read `{"errors": [{"id": …}]}`, and the quotation
  endpoint — the one that produces these codes most — answers
  `{"message": "ERR_OUT_OF_SERVICE_AREA"}` with no array at all. So `error_id`
  was `None`, checkout told customers "Courier quote failed" for an address that
  was simply out of range, and `batching_service` kept retrying dispatches that
  could never succeed.
- **Why it survived**: nothing tested the parser, and every caller degrades
  gracefully on the `None` branch. A predicate that is always false produces the
  fallback behaviour, and the fallback behaviour is not an error.
- **Rule**: a vendor with two documented error shapes needs a test per shape,
  pinned from their docs rather than from whatever we happened to receive first.
  And when a branch exists for a specific error id, grep for one real response of
  that kind before trusting that the branch has ever been taken —
  `SELECT ... FROM webhook_logs` and the vendor's own error reference are both
  cheaper than the bug.


### [2026-08-05] A promise is a fact about what was said, not a calculation to repeat
- **What went wrong**: the checkout and the confirmation email each derived the delivery estimate independently. Both were correct; they answered different questions. Checkout read the batch window open *at that moment* (`dispatch_at + 1h` = 19:00); the email ran at CONFIRMED, before any batch is assigned, and fell through to a generic `created_at + 2h prep + 1h drive` (17:25). MM-20260805-008 told the customer two different times in the same minute.
- **Why the obvious fix was wrong**: re-deriving the window at send time. By then the window that was open at checkout may have closed, so the customer would be silently moved onto a later run nobody had mentioned — a *different* wrong answer, and a harder one to notice.
- **Rule**: when a number is shown to a customer, store it. Anything the shop has said out loud is a record, not a derivation, and the only things allowed to overrule it later are events that actually happened (a rider collecting) or a real change of plan the customer needs to know about (the order missing its window and moving to the next run). If two code paths compute the same customer-facing figure, that is the bug, whichever one is "right".


### [2026-08-05] Ask what the gate is actually protecting before building around it
- **What went wrong**: I planned an elaborate flow for accepting a website order in companion mode — switch to register, prompt to open a till, then accept — and offered it as the recommended option. Hussain asked "why does a website order need a till in the first place?" It does not: it is already paid for, `accept_order` never touches `till_id`, and the ticket had always printed with `openDrawer: false` for exactly that reason. The till requirement was an artefact of *where the queue had been attached* (the register screen, which only exists at `.ready`), not a rule anyone had decided.
- **What it was costing**: a counter iPad on the till-open screen at 8am saw no website orders until somebody counted a drawer, and a manager's phone — which defaults to companion mode — never saw one at all, nor even registered for the push.
- **Rule**: when a constraint forces an awkward flow, find the line of code that imposes it before designing around it. A constraint nobody wrote down is usually a side effect of structure, and the fix is to move the structure. Offering the user three ways to live with a phantom rule is worse than spending five minutes proving it is phantom.


### [2026-08-05] SwiftUI reuses views by structure, so encoding a choice as `if let` on a closure will strand it
- **What went wrong**: `IncomingOrderCard` took `onAccept:` and `onMarkPacked:` as optional closures and rendered `if let onAccept { … } else if let onMarkPacked { … }`. When an accepted order moved from the waiting list to the kitchen list — two sibling `ForEach`es in one `LazyVStack`, same `order.id` — SwiftUI matched the identity across them and kept the branch the view was already in. The card sat under "In the kitchen" saying "Accepting…" indefinitely.
- **How it surfaced**: only by driving the simulator. It compiled, 158 tests were green, and both apps built.
- **Rule**: express "which of these does this row do" as a value the row's own state decides (an enum), never as view structure. And give each list's rows an id namespaced by the list (`"waiting-\(id)"`), so an item moving between sibling lists cannot be transplanted. Corollary: a POS change that only builds and unit-tests clean is not verified — CLAUDE.md already says look at it on both devices, and this is what that step is for.


### [2026-08-04] A substring search over a whole HTML document is not a test of an attribute
- **What went wrong**: to assert an email rendered in Arabic I checked `'lang="ar"' in html`. It passed — and it passed for the English email too, because the stylesheet in every email contains the Arabic rules `[lang="ar"] .tagline { … }`. The document always contains that string. The test asserted nothing and would have gone on asserting nothing after the feature broke.
- **How it surfaced**: only by running two real orders end to end and printing what came out. The English one reported `lang="ar" dir=rtl` while its subject, its branch card and its links were all correctly English — which is what made it obvious the *check* was wrong rather than the code.
- **Rule**: when asserting on an attribute of a specific element, extract that element first (`re.search(r"<html[^>]*>", …)`) and assert on it. A document-wide `in` check is only safe for a string that can appear in exactly one place, and in an HTML document with an inline stylesheet almost nothing qualifies. And when a test and an end-to-end run disagree, believe the end-to-end run until you know which one is lying.


### [2026-08-04] `is_pos` is not the channel — a website order is a POS order too
- **What went wrong**: nearly gated "don't email counter sales" on `orders.is_pos`. It reads like the flag for a till sale and it is not: `attach_online_order` sets `is_pos = True` on every storefront order, because an order a kitchen has to bake is a POS order in every operational sense and that is what `/pos/orders`, the dispatch board and the operations screens filter on. Gating on it would have silenced **every** customer email the shop sends, and no test in the suite would have failed — the fixtures build `OrderResponse` by hand and none of them carried the flag.
- **Why it was tempting**: the request was "POS orders should not send customer emails", and there is a column called `is_pos`. The column that actually means "rung up at a till" is `source`, which is what the admin orders screen already filters its channel tab on.
- **Rule**: before gating behaviour on a boolean, find every place that *writes* it, not just the places that read it. And when a distinction already exists somewhere in the product (here, the admin's Website/Counter tab), reuse its predicate rather than inventing a second one — two definitions of the same thing will disagree eventually, and the one in the mailer is the one nobody watches.


### [2026-08-04] Pydantic coerces a MagicMock to `True`, so a bool default in a test fixture asserts nothing
- **What went wrong**: `_order()` in `test_order_service.py` builds a `MagicMock` and sets every column by hand, but never set `email_has_account`. `OrderResponse` has `from_attributes=True`, so validation read the attribute off the mock, got a `MagicMock`, and coerced it to `True` — the opposite of the field's default. Every test in that file had been quietly asserting a value nobody chose.
- **Why it hid**: it only breaks on a *typed* field. Adding `fulfilment: FulfilmentResponse | None` raised immediately and 31 tests went red at once; the bool had been wrong in silence for as long as the field had existed.
- **Rule**: when a mock stands in for an ORM row on a model with `from_attributes=True`, set **every** field the response declares, including the ones with defaults. A field left to the mock is not a default — it is whatever pydantic can coerce a `MagicMock` into, and for `bool` that is always `True`.

### [2026-08-04] A `translations` column has one shape; check which one before reading it
- **What went wrong**: `Branch.name_for` did `self.translations.get(locale)` and returned it as a string. The column holds `{locale: {field: value}}` — the shape `Translations = dict[str, dict[str, str]]` declares in the schemas and the shape the storefront's `localizedField` reads — so it returned a dict, failed the truthiness-then-`str()` path, and fell back to English for every branch that had translations at all.
- **Why it hid**: the method had no callers. It was written alongside a model that had no Arabic data to read, so it was never wrong about anything until a branch needed a name in two languages.
- **Rule**: before reading a JSONB translations column, find one other model that reads the same column name and copy its access pattern. And a helper with no callers is not tested by anything — either give it one or do not write it yet.


### [2026-08-04] Check the branch is current before designing on top of it
- **What went wrong**: six commits of courier and POS work were built on a `main` that was 26 commits behind `origin/main`. The remote had since added dynamic delivery pricing, a `pricing_mode` flag on `delivery_polygons`, a free-delivery scope change and a batch-retry path — none of it visible locally. The result is three colliding migration numbers, two Alembic heads at `056`, and a delivery model that has diverged in both directions.
- **Why it hid**: everything built, every test passed, and the local history looked linear. `git log origin/main..HEAD` reads "6 ahead" whether or not you are also behind; only the `[behind N]` in `git status -sb` says the other half.
- **Rule**: `git fetch && git status -sb` before starting any change that adds a migration or touches a shared model, and again before writing the plan. A plan written against a stale base is wrong in ways no test can catch.

### [2026-08-05] `docker logs` is not the record — and "no request arrived" needs the durable one
- **What went wrong**: I told the user noon Send had never sent a status webhook. They had sent three. I checked `docker logs`, which only covers since the last container restart, and there had been five deploys in the intervening forty minutes. The pushes were in Cloud Logging the whole time, which the compose file explicitly configures and which I had used earlier in the same session for the Stripe investigation.
- **The second half**: the pushes were *rejected*, with `Rejected noon Send webhook: the API key does not match` — and the rejection path returned before the `webhook_events` insert, so the durable journal had no record either. Two independent-looking sources agreed on "nothing arrived" and both were blind for different reasons.
- **Rule**: "we never received it" is a claim about the whole retained history, so query the durable sink — `gcloud logging read`, not `docker logs` — and say which one you checked. And log an inbound webhook *before* deciding anything about it: a request refused at the door leaves no trace anywhere that a rejection is not itself journalled, which is precisely when you most want one.

### [2026-08-05] The four-place secret checklist was missing its fifth place, and the gap is silent
- **What went wrong**: `noon Send`, APNs and Turnstile were all added to `.env.example`, `PRODUCTION.md` and both deploy workflows — every place the repo's own checklist named — and none of them to `docker-compose.prod.yml`. That file's `environment:` block is an **allow-list, not an `env_file`**, so a variable written to `.env` on the VM and not named there never reaches the container. Production had been running with noon Send entirely inert, no push notifications to any register, and the bot check off. Every secret was on the VM the whole time.
- **Why it hid**: an unset integration key reads as "this integration is switched off", which is a legitimate state. `is_enabled()` returned false and the code did exactly what it should — fell back to Lalamove, skipped the push, allowed the signup — so there was no error anywhere, and I reported all three as configured because the secrets were present where I looked.
- **The tell I missed**: the compose file already carried a comment saying this block is an allow-list and that leaving a variable out ships the integration inert. It was written after the same mistake with Lalamove. I read it and did not apply it.
- **Rule**: after setting any production secret, verify it reaches the process rather than the file — `docker exec <container> printenv | grep KEY`, or better, `comm -23` the `.env` keys against the container's. "The secret is on the VM" is not the same claim as "the app can see it". `test_compose_env_allowlist.py` now fails when a `Settings` field cannot be configured on production.

### [2026-08-04] An empty table must never be how a 500 looks
- **What went wrong**: `AuditLogItem.id` and `EmailLogItem.id` were typed `str` against `UUID` columns, and both models are populated straight from the ORM row. Pydantic v2 does not coerce `UUID` into `str` — it raises — so **every** response from both endpoints was a `ResponseValidationError`. The admin's audit and email screens had been showing nothing for months while the tables filled up behind them: 69 and 71 rows, the newest from the day it was reported.
- **Why it survived**: both pages caught the failure with `} catch { // silent }`. An empty table is a perfectly good answer to "no results", so it is indistinguishable from "the server refused" — and ten admin pages were written that way. Nobody reports a bug they cannot see.
- **Rule**: a list screen needs three distinct states — loading, empty, and failed — and the third one has to say so on screen. `// silent` in a catch is a bug waiting for someone to notice. And when a response model reads from an ORM row, build it from a real model instance in a test: the mismatch lives in the gap between the column type and the field type, which no field-by-field assertion will find.

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

### [2026-08-05] Check the read path before concluding the write path is broken
- **What went wrong**: "None of the cart/checkout events are coming through" was
  read as a tracking failure, and the first three hypotheses were all about
  sending — ad blockers, navigation aborting the fetch, a bad auth header. Each
  was disproved by measurement. The actual finding was that nothing in the
  codebase ever *read* custom events back: `/analytics/traffic` fetched stats,
  pageviews and paths and stopped there.
- **Rule**: For any "the data isn't arriving" report, establish which end the
  observation was made from before theorising. If the report is "I can't see it
  in our dashboard", check what our dashboard queries first — it is cheaper than
  auditing the sender and it is where this one was.

### [2026-08-05] Verify third-party client behaviour against the served file
- **What went wrong**: Reasoned confidently that a hard `window.location.href`
  after `umami.track()` would abort the event, because Umami's tracker used a
  plain `fetch`. Curling the actual script showed `keepalive: true`, and a
  Chromium reproduction confirmed the request survives navigation — even across
  a 307.
- **Rule**: The vendor's script is one `curl` away and the browser is installed.
  Read the code that is actually being served, and reproduce, before building a
  fix on top of remembered behaviour.

### [2026-08-05] A swallowed error is a wrong answer with a straight face
- **What went wrong**: `/analytics/traffic` returned zeros for a refused API
  key, an unreachable host, an unparseable reply and a genuinely quiet week —
  and reported `configured: true` next to all four. This repo's own notes record
  a 403 from that endpoint months earlier; nothing on screen could have said so,
  so it stayed unfixed and the numbers were read as real.
- **Rule**: `except Exception: return empty` is only acceptable when empty and
  broken are the same thing to the reader. When they are not, carry the reason
  to the surface — a dashboard that says why it is blank costs one field and
  saves the next investigation entirely.

### [2026-08-06] A live value equal to the default proves nothing about the environment
- **What went wrong**: Before renaming the analytics paths, checked whether
  `NEXT_PUBLIC_UMAMI_URL` was set in Vercel by curling production and reading
  the script `src`. It was `/umami/script.js` — which was *also* the code's
  fallback, so the observation could not distinguish "unset" from "set to the
  same string". Concluded it was unset and said so. It was set. The rename
  shipped, the tag pointed at a path that had just become a 404, and the
  tracker stopped loading for about ten minutes. `data-host-url` moved with the
  code, so the markup looked correct and only the `src` was wrong.
- **Rule**: An observation that both hypotheses predict is not evidence. Before
  concluding an override is absent, either read it where it is configured, or
  find a signal the two cases disagree on. And when the check cannot be made,
  say the value is unknown rather than assuming the default — the deploy will
  find out either way, and it is the wrong place to find out.
- **Corollary applied here**: paths internal to the app (the script rewrite, the
  send route) are no longer environment-configurable at all. An environment that
  disagrees with the code about them is a fault, not a deployment choice, so
  there is nothing to get out of sync.
