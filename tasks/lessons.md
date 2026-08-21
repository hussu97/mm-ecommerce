# Melting Moments Ecommerce - Lessons Learned

> Updated as mistakes are corrected. Review at session start.

## Project Context
- UAE-based artisanal bakery (Melting Moments Cakes)
- Turborepo monorepo: Next.js 15 storefront + admin, FastAPI backend, PostgreSQL
- Design: dusty mauve (#8a5a64), Playfair Display + Jost, mobile-first, clean minimal
- ~33 SKUs across 5 categories, delivery across UAE

## Lessons

### [2026-08-18] A fix that needs somebody to run it is not a fix
- **What went wrong**: three separate changes the shop had asked for — noon Send
  60→90, and two rounds of FAQ copy — all shipped as `scripts/` files because
  CLAUDE.md §7 said content edits live there. Each deploy went green and none of
  the three changed anything a customer could see. I reported that honestly each
  time, which made it look handled; it was not. Hussain's answer was the obvious
  one: *why don't you do all of this as a migration, which auto-runs on deploy —
  you have done previous CMS changes the same way.*
- **He was right about the precedent, and I never checked it.** `008`, `009`,
  `054`, `058` and `061` are all CMS content in migrations. I read the rule,
  matched "CMS copy" to "scripts/", and never asked why every existing example
  contradicted it. A rule and five counter-examples in the same repo means the
  rule is stale, not that the examples are wrong.
- **The real distinction**, now written into §7: a *script* is an operator tool
  a human chooses to run; a *migration* is for a change that has to land. "Is
  this content?" was never the right question. "Does this need to be true the
  moment the deploy finishes?" is.
- **What the original rule was protecting against is real**, and is solved by a
  guard rather than by exile: match the exact value being replaced (`WHERE
  minutes = 60`, whole-string swaps) so that once somebody edits it in the
  admin, the migration matches nothing — including when replayed against an
  older dump. That is three lines, not a separate delivery mechanism.
- **Rule**: before shipping a change as a script, ask what makes it true in
  production and who does it. If the answer is "a human, later, from a shell I
  cannot reach", it belongs in the deploy. And when a documented rule disagrees
  with every instance of it in the codebase, resolve that before following it.

### [2026-08-18] Counting occurrences in rendered HTML counts the framework, not the content
- **What went wrong**: I reported "9 occurrences of `home kitchen` on the live
  FAQ page, I fixed 2, so 7 remain" from `curl | grep -c`. There were **two** in
  the FAQ. The other seven were the same two strings repeated in the JSON-LD
  block and again in Next's RSC flight payload, which embeds the rendered tree
  in the HTML. Hussain then asked me to fix seven things that did not exist.
- **Why it was convincing**: the number was real and reproducible, and it was
  next to a claim I had verified properly (the live page genuinely still showed
  the old copy). One measured fact next to one miscounted one reads as two
  measured facts.
- **What the count should have been**: eight, across three CMS pages — the two
  FAQ answers plus the `home` and `about` `seo.description` fields, in both
  locales. Found in seconds by fetching `/cms/public/{slug}?locale=…` and
  walking the JSON, which is the source the page renders *from*.
- **Rule**: count in the data, never in the markup. For anything CMS-backed that
  means the API's JSON or the `cms_pages` row; a grep over server-rendered HTML
  triple-counts every string a modern framework emits. And when reporting "N
  remaining", say where the N came from, so a wrong one is arguable rather than
  actionable.

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

### [2026-08-08] A speedup measured on this laptop is not a speedup in CI
- **What went wrong**: Added `pytest -n auto --dist loadfile` to both workflows
  because it halved the suite locally (9.6s -> 5.6s on ten cores), and I had
  gone to some trouble first proving it was *safe* — checked the three real-DB
  modules for colliding cleanup prefixes, ran them twice against a throwaway
  Postgres to catch leftover rows. All of that was sound, and none of it was
  the question. On the runner it measured 50s against 47s serial: slightly
  worse. ~1280 tests that each take a millisecond, whose real CI cost is
  importing the app once — which every xdist worker then pays again. I had
  projected "46s -> ~20s" in the write-up before any of it ran.
- **Rule**: Parallelism pays when the work is the bottleneck. Before splitting a
  job across workers, find out what the seconds are actually made of — startup,
  import, download, or execution — because only the last one divides. A local
  timing on different hardware answers a different question than the one CI is
  asking.
- **What worked instead, and why**: the three changes that held up all deleted
  work rather than redistributing it — 149s of re-encoding images already
  committed to the repo, 165s of uploading files one request at a time, 19s of
  pip resolving what uv resolves in 2. Look for work that should not happen at
  all before looking for work to spread around.
- **Also**: I only caught this because I pulled per-step timings from the run
  *after* pushing and compared them to a baseline run with the same job set.
  Shipping a CI change without measuring the result is how a regression sits
  there looking like an improvement — the projection in the commit message
  would have been the only record, and it was wrong.

## Check the machine-readable surfaces when a commercial fact changes

Asked to fix a coupon's UX, I first treated "SEO/GEO" as the homepage. The real
damage was in files no human opens: `llms.txt`, `llms-full.txt` and
`ai-plugin.json` each hard-coded "New customers get 15% off their first 3
orders" with no code, while the live row was 20% with the code `NEW`. Those
files are written *for* answer engines, cached for an hour to a day, and read by
people who never load the site — so a stale figure there is quoted back as fact
and the checkout then refuses the customer who arrives on it.

**Rule:** when changing a price, discount, fee, threshold or delivery promise,
grep the whole repo for the *old figure* before finishing — not just for the
component that renders it. If more than one surface states a commercial fact,
they must all read it from one function (`lib/offer.ts` is the pattern), because
the ones that drift are always the ones nobody looks at.

## Do not commit into another session's working tree without asking

The session-start git status said "clean"; by the time I ran `git diff`, six
files I had edited also carried an unrelated in-flight feature. That snapshot is
taken once and never refreshes. **Rule:** run `git status` immediately before
staging, and if unrelated work is present, surface it and let the user decide
rather than sweeping it into a commit under their name.

## A migration is not verified until a real Postgres has run it

`092` was written with the revision id `092_cart_addons_and_personalisation`.
`alembic_version.version_num` is `varchar(32)`; that string is 35 characters. On
`alembic upgrade head` every DDL statement ran and the migration then failed
writing down *that* it had — leaving a database carrying the new columns with no
record of the revision, which is the one state that cannot be re-run or rolled
back cleanly.

The full API suite — 1290 tests — passed throughout. It mocks the database, so a
migration that cannot apply is invisible to it, and CI would have gone green all
the way to a deploy that broke on the VM.

**Rule:** never call a migration done on the strength of the test suite. Run
`alembic upgrade head`, then `downgrade -1`, then `upgrade head` again against a
throwaway Postgres, and assert the columns and indexes are present after each
upgrade and gone after the downgrade. Keep revision ids **≤32 characters** —
prefer `NNN_two_or_three_words` and drop the conjunctions.

## Price the suspect before rewriting it

A $4.04 charge was reported as phone verification. I accepted the attribution,
divided by the $0.09 UAE SMS rate, wrote "roughly 45 billed messages" into a
commit message, a lesson and a changelog — and then measured. Cloud Monitoring
said `SendVerificationCode` had run **three times in twelve days**. The $4.04 was
a `e2-micro` VM with a 20 GB pd-ssd and a static IP in Doha, running since March,
on a billing account shared with three other projects. Phone auth was ~$0.

The arithmetic was fine. It was reasoning downward from a number to a mechanism,
which produces a confident count out of an assumption. **Rule:** when a cost is
attributed to a feature, count the underlying calls *before* touching code —
`serviceruntime.googleapis.com/api/request_count` grouped by service, then by
method, settles it in one query. Check which projects share the billing account.
Never state a derived volume ("~45 messages") as if it were observed, and never
write a number into a durable file that a single query could have confirmed.

## Put the guard in front of the step that spends money

Verification code had no cooldown, no ceiling, and never asked whether the number
was already proved. Little had been spent through it — but the exposure was real
and the shape is worth keeping.

The fault was placement. `signInWithPhoneNumber` runs browser→Google, so the
paid step never touches our server — and both our controls, Turnstile and a
`10/minute` limiter, sat on `/auth/verify-phone`, which runs *after* the SMS is
bought. We rate limited the free action and left the billed one open. The module
docstring even said so — "Turnstile here guards our ledger rather than Google's
SMS bill" — which is the tell: the observation had been made and read as a note
rather than as a defect.

Three specifics fell out of the same blind spot. "Resend code" had no cooldown,
so one impatient customer was worth $0.09 a click. Nothing capped the sends.
And the checkout never asked `/auth/phone-verified` before sending, though the
endpoint existed and the account page already called it — so a returning
customer's proof was on file and got paid for again.

**Rule:** for any third-party call billed per invocation, find where the money
is actually spent and ask what stands in front of *that* line, not in front of
the request that records the result. Then check the four in order: is there a
cooldown on every control that can re-trigger it; is there a ceiling; is the
existing answer consulted before buying a new one; and does our own bot check
run before the spend or after it. Client-side guards only stop honest
over-clicking — anything the vendor enforces (region policy, App Check, fraud
scoring) is a console setting, so "the code is fixed" is half an answer and the
console half needs naming explicitly to whoever owns the account.

## A third-party tag can be present, reachable, and still dead

Microsoft Clarity shipped as `<Script src="https://www.clarity.ms/tag/<id>">`.
Everything an inspection would check was true: the tag was in the DOM, the
project id was right, the URL returned HTTP 200, and the CSP allowed it. It
recorded nothing at all.

The file that URL serves is a 712-byte loader whose first statement is
`a[c]("metadata", …)` — it *calls* `window.clarity` and pushes onto
`window.clarity.q`. It never defines either. Microsoft's install snippet is an
inline stub that defines the global as a queue and *then* injects that loader,
and pointing `src` straight at the loader skips the half that makes it work. It
threw `a[c] is not a function` on its first line and never fetched the real
recorder. `window.clarity` stayed undefined forever, so every mirrored event was
dropped while Umami counted normally and the two dashboards silently disagreed.

**Rule:** a vendor's install snippet is a contract, not boilerplate to tidy. When
one is inline, ask what it defines before replacing it with a `src` — a stub that
buffers early calls is the usual answer, and collapsing it is invisible until the
data does not arrive. Verify a tag by the global it is supposed to install
(`typeof window.X === 'function'`, and the second-stage script in the network
list), never by the presence of the script element. And when a minified
`X is not a function` appears in a console, chase it: it is the only symptom this
class of failure produces.

## A response model is a place a 500 hides

`POST /delivery/quote` returned 500 for every call made before an address pin
existed — which is the checkout's *first* call, on every session. The handler was
correct throughout. `price()` had been changed to return `free_threshold=None`
before a pin, because thresholds stopped being national when the outer zones went
fixed-fee; `DeliveryQuoteResponse.free_threshold` was left as a required `float`.
FastAPI validates the response, so the null raised `ResponseValidationError`
*after* the work was done, and it surfaced as our fault in the logs.

The storefront had already been written for `number | null`. Every layer agreed
except the annotation between them.

**Rule:** when a service function's return value gains a `None`, grep the
response model that serialises it in the same change — the type checker will not,
because the handler returns a `dict`. And write the regression test through the
response model, not the service dict: a test asserting on the dict passes while
production 500s, which is exactly what the existing delivery tests did.

## Don't narrate the machinery, and don't style waiting as failure

I added a Turnstile gate to the "Send code" button and, when the token had not
arrived yet, refused the click and printed **"Still running the security check —
one moment."** in red, below the form.

Three faults in one line. It described an internal mechanism the customer never
asked about and cannot act on. It used the error slot and error colour for
something that was not an error and needed nothing from them. And it *refused*
work the customer had explicitly asked for, when the honest response to "not
ready yet" is to wait — the button was right there, able to say "Sending…".

The correct shape: the control that was pressed shows what is happening, the
work proceeds as soon as it can, and a message is only written when there is
something the person must know or do.

**Rule:** before writing user-facing copy, ask who the sentence is for. If it
names a vendor, a check, a token, a queue or a retry, it is for me and belongs in
a comment or a log. Waiting states belong on the control that was pressed, never
in the error slot — reserve that for something the reader can act on. This
applies to loading, retrying and rate-limiting alike.

## An empty state is a claim, and it needs the data to make it

The cart page rendered "YOUR CART IS EMPTY" on its very first paint. The guard
was `!isLoading && items.length === 0`, and `isLoading` is the *mutation* flag —
`refreshCart` never sets it. So before the first fetch returned, `cart` was
`null`, `items` was `[]`, and the page confidently told a returning guest their
basket was empty. Adding anything re-rendered with the fetched cart and their
old items appeared, which reads as items materialising out of nowhere.

The context already exported `cartLoaded` for precisely this question, and the
checkout already used it. Only this page did not.

**Rule:** "no data yet" and "no data" are different states and must render
differently. Any empty state, zero count or "not found" needs a loaded flag in
its condition — absence of data is never on its own evidence of absence. When a
context exposes both a mutation flag and a loaded flag, check which question each
answers before reaching for one.

## A permission that no route checks is a lie told to whoever ticks the box

The role catalogue was the Foodics authority matrix copied whole: 108 slugs, 61
of which no `require(...)`, `ensure(...)` or `.can(...)` in the codebase ever
named. Some named features that were never built (allergens, timed events,
count sheets) or had been deleted along with their tables (reservations, table
layouts, price tags). The rest split rights nobody splits — two permissions for
pressing print, four for reading a cost report.

The granularity was the visible half. The invisible half was worse: `User.can()`
short-circuits on `is_admin`, and 160 console routes asked only for
`get_admin_user`, so every one of the 21 `admin.*` slugs was decorative. A role
could be built, named "Back office", handed out, and change nothing about what
its holder could reach. There was no way to grant the branches screen without
also granting payment gateways, staff roles, data export and the audit log.

**Rule:** a permission earns its place in a catalogue by being enforced on a
route, and nothing else. When adding one, wire it in the same commit; when
deleting a route, delete its slug and record where holders land. Enforce it with
a test that reads the source for every slug — `test_permissions.py::
test_every_permission_is_enforced_somewhere` — because a catalogue nobody can
grep against drifts back into a wish list within a release.

**Corollary, and the sharper trap:** the moment the role editor became delegable
(`admin.users.manage` rather than `is_admin`), that single permission was worth
all the others — write a role holding everything, or flip `is_super_admin`,
assign it to yourself. Any permission that can *grant* permissions needs a
downward-only bound (`assert_no_escalation`), or it is not one permission, it is
the whole set.

**Second corollary:** when consolidating a stored vocabulary, the old→new map is
the deliverable, not a side note. It belongs next to the catalogue, duplicated
into the migration that applies it (a migration must keep describing the
database as it was), and compared by a test so the two copies cannot drift.

## Two statuses can be legal neighbours and still be a hole in the flow

`arrived_at_pos` was introduced so an order reaches the register when the van is
booked rather than when the money lands. `confirmed → packed` stayed in
`VALID_TRANSITIONS` — reasonably, since a rider can collect a box our own
bookkeeping never caught up with — and `stamp_packed` fires on every successful
courier booking. So the first order to take the new path (MM-20260820-002,
dispatched by hand out of an 08:00 run at 05:31) went `confirmed → packed` and
skipped the only status `publish_to_register` hangs off. Lalamove had a driver
on the way; the kitchen had no check number, no ticket, no alarm and nothing to
accept. The arrival sweep could not recover it either — that sweep looks for
orders still at `confirmed`, and this one had already gone past.

The batched path had the same hole and it was not an edge case there: a run
being booked stamps every order on it packed, in the same sweep tick that would
otherwise have landed those orders a moment later.

**Rule:** when a new status is inserted into a flow, the shortcut past it is
still in the transition map — go and find every writer that can take it. Ask of
each: does this transition skip a consequence keyed on the status I just added?
A consequence keyed on `new_status` fires only for the orders that actually pass
through it, and a legal shortcut is silent. Fix at the choke point the shortcuts
share (here `stamp_packed`), not at each caller.

**Corollary:** an order that leaves a shared run has to tell the run. The
delivery row knew it had its own booking; the batch went on counting it as a
stop and stayed scheduled to collect it. State that lives in two rows needs the
write that updates one to update the other in the same call
(`cancel_assignment`), or the correction waits for whatever sweep happens to
notice — hours later, if at all.

## An idempotency key is a promise about the payload, not just the endpoint

`Session.create` was called with `idempotency_key=f"sess_{order_number}"` —
correct, and the behaviour we want: one order, one payment page, whichever tab
the customer is on. Two lines above it, the discount was applied by calling
`Coupon.create` with no id, which mints a *new* coupon every time. So the
`discounts` argument under that fixed key was different on every attempt, and
Stripe's rule is that one key carries one payload or it carries nothing: the
second time a customer opened the payment page for a discounted order, it
answered 400 `idempotency_error` and no page opened at all.

It never showed up as an error anybody saw. The first attempt always works, so
it fires only for a customer who reaches Stripe, changes their mind, and comes
back — who then has no way forward and places the order again from scratch, which
looks like ordinary indecision in the orders list. It was found in Stripe's own
Health tab, not in ours.

**Rule:** when you pin an idempotency key, every argument under it has to be a
function of the same thing the key is. Anything minted, timestamped or random in
that payload — a coupon, a nonce, a "created at" — makes the key a trap that
springs on the second call. Name such resources after the entity
(`order-MM-20260820-001`) and reuse them, so "the same request" stays the same
request.

**Corollary:** put a fallback under any pinned key on a customer-facing path. A
dead checkout button is the worst outcome available, and it is what payload
drift always produces. Catching the refusal and opening a fresh session costs a
duplicate that nothing can pay twice.

---

## A guarded migration that matches nothing looks exactly like one that worked

**2026-08-21.** `118` rewrote the free-delivery figure across `cms_pages` and I
reported it done. It was not: the same claim also sat in `ui_translations` as
`promo_banner.text`, the strip across the top of every page on the site, in both
languages. Deployed, went green, and left the most-read sentence the shop
publishes still promising free delivery over AED 150 when the checkout gives it
away at 75.

`109` opens with the same admission about `107`, three months earlier, and even
names `ui_translations` as the table `cms_pages` sweeps miss. I did not read it
until after I had repeated it.

Then the fix repeated the shape of the mistake. `121`'s first draft matched
`key = 'promo_banner.text'`, which is the name the *site* uses — `i18n_service`
builds it on the way out with `f"{t.namespace}.{t.key}"`. In the database those
are two columns, `namespace='promo_banner'` and `key='text'`. It would have
deployed green and changed nothing. It only surfaced because the throwaway
database was seeded by the real migrations and disagreed with the fixture I had
hand-written to match my own assumption.

**Rule:** a content migration is only verified when it has been run against a
row seeded by the migrations, in the state production is actually in. A fixture
you wrote yourself tests your assumption, not the schema — and a guarded
migration that matches nothing is indistinguishable from one that worked, from
every angle except the site.

**Rule:** the guard cuts both ways. `WHERE value = :old` is what stops the
migration fighting the admin, and it is also what makes a wrong `WHERE` silent.
So assert the *after* state, never just that the migration ran — and for content,
assert it against the public endpoint the site reads rather than the table.

**Corollary:** before writing a content migration, walk `/cms/public`,
`/i18n/translations` and `/blog/public` for the string. That is how `109` found
its own leftovers and it is the only method that has worked here. Grepping the
repo finds the seed migrations, which are history — several have been superseded
and the console has edited rows since.

---

## A migration cannot change a UI string, and the answer was already in this file

**2026-08-21.** Two migrations — `121` then `122` — went to production to change
one line of `ui_translations`. Both applied. Both deployed green. Neither
changed anything that lasted more than a few seconds.

`app_setup` runs `scripts.seed_i18n` in the API's lifespan hook, so it executes
on **every boot** and overwrites any row whose value differs from its constant.
The deploy order is migrate → restart → seed, so each migration edited the row
and the seed restored it before anyone could look. Then the seed invalidated the
Redis cache, so the restored text was serving immediately. Nothing about this
appears in the migration's own output: it prints `Running upgrade …` and exits 0.

I spent an hour ruling out Redis TTLs, CDN caching, the namespace/key split, the
locale codes, and byte-level differences in the string — and comparing values
against an API whose answer the seed had already put back. Every measurement was
consistent with "the WHERE clause matched nothing", and none of them could
distinguish that from "it matched, and was reverted".

**The rule was already written above, in April**: "`main.py` lifespan hook
imports `seed_i18n` and runs it on every startup". I did not read this file at
the start of the session, which CLAUDE.md asks for, and then wrote a lesson
about verifying against production without noticing the one already there.

**Rule:** `scripts/seed_i18n.py` is the source of truth for UI strings, not the
database. To change one, edit that file. A migration against `ui_translations`
is undone on the next boot — as is an edit made in the console's Translations
screen, which is why a value there can disagree with the seed.

**Rule:** to *retire* a key, both halves are needed. `seed()` only adds and
updates, never deletes, so removing the line stops it being restored but leaves
the existing row; the row needs a migration. One without the other looks like it
worked and does not.

**Rule:** when a content change deploys green and the site is unchanged, ask
what else writes that table before investigating what could be caching it. A
seed, a webhook or a sync job re-asserting the old value looks exactly like a
stale cache from the outside, and the two are told apart by reading the writers,
not by measuring the reads.

---

## A test double that autoflushes hides every read-your-own-writes bug

**2026-08-21.** MM-20260821-001 dispatched correctly at its 15:00 window — batch
sent, register told, Lalamove booked — and then lost its driver entirely. The
webhook that named the rider rolled back at commit on
`uq_order_driver_active`, having already answered **200**, so Lalamove never
resent it. The order sat through its whole delivery with no driver on the
ledger, no name on the register's slip, and a `courier_status` frozen at
`ASSIGNING_DRIVER` — because every later webhook hit the identical path and
rolled back identically.

The mechanism: `AsyncSessionFactory` is built `autoflush=False`. A row that is
only `db.add`ed is invisible to any later `SELECT` in the same transaction.
`driver_assignment.record()` opens a stint, the caller then reaches
`fill_driver_details()` which comes back through `record()` a second time, and
that second call's `active_driver()` could not see the row the first had just
added. It read `current is None` as "a booking older than this table" — the
adoption branch — and opened a second live stint. Two rows, one flush, one
constraint.

Ten unit tests covered `record()` and all ten were green, because the fake
session appended straight onto the list its `execute()` read from. **The double
was read-your-own-writes when the real session is not.** Making it faithful —
`add()` holds a row pending, `flush()` publishes it and raises on two live
stints — turned six of those existing tests red immediately, without touching
their assertions.

**Rule:** a fake session must model `autoflush=False`, because that is what the
app runs. `add()` goes to a pending list; only `flush()` publishes. Any fake
whose `add()` is immediately visible is asserting a database we do not have.

**Rule:** where a table has a unique constraint the code depends on, the fake
enforces it at `flush()`. It is the one line that turns "these objects look
right" into "Postgres would accept these" — and it is cheap next to the real-DB
test the constraint would otherwise need.

**Rule:** `commit()` happening in `get_db`'s teardown means an IntegrityError
surfaces *after* the route has returned its status line. A 200 in the access log
is not evidence the transaction committed. When a webhook's effect is missing
and the log says 200, check for an unhandled exception with no `request_id` at
the same second before believing the sender never called.

**Corollary:** recovery sweeps inherit the hole. `driver_tracking` refreshes only
deliveries `WHERE driver_id IS NOT NULL`, so the one order whose driver write
was lost is precisely the one the sweep will never revisit. A backstop keyed off
the value the bug destroys is not a backstop.

---

## Owning a transition is not the same as receiving one

**2026-08-21, following the driver-ledger bug above.** Closing that one exposed
two more of the same shape, and the shape is the lesson.

`driver_tracking` deliberately left every terminal transition to the webhook —
correct about ownership, and silent about what happens when the webhook does
not arrive. We had just proved they can be lost: the route answers `200` before
`get_db` commits, so Lalamove logs a delivery for a transaction that rolled
back, and there is no retry to wait for. A lost `COMPLETED` leaves a cake in
the customer's hands and the order at `packed` until a person notices.

The sweep already held the authoritative answer — it reads the booking on every
tick — and was throwing it away with an early `return False`.

**Rule:** if a push owns a transition, something that polls must be able to
apply the same transition when the push is missing. "The webhook owns it" is an
answer about *who writes it*, never about *whether it gets written*.

**Rule:** reconcile by feeding a fabricated payload through the real handler,
never by writing the columns again beside it. Ending a booking here is the
order's transition, the customer's email, the refund path, the price, the
proof, the cancellation reason — a second copy of that is a second thing to
keep in step with `VALID_TRANSITIONS`, and it will drift. `apply_webhook`
already takes an optional delivery precisely so a caller holding one can reuse
it.

**Rule:** a fabricated payload must not borrow a real event name. Their names
promise a body — `DRIVER_ASSIGNED` means a whole person is described — and
`last_payload` is the first thing anybody reads when asking what the courier
said. Name it for where it came from and let the branches fall through.

**Rule:** stamp a reconciliation *now*, not with the courier's own moment. The
out-of-order guard drops anything older than the last applied update, and the
genuine event may well predate a push we did receive — so the honest stamp is
also the only one that works.

**Rule:** when a filter is opened up, ask what now keeps it bounded. Removing
`driver_id IS NOT NULL` and admitting `ASSIGNING_DRIVER` meant rows could sit
in the live set indefinitely; the reconciliation is what lets them leave it,
and `CHASE_FOR` is the backstop for the case where even that fails.

**Corollary, on tests:** the mocked version of this asserts the sweep *calls*
`apply_webhook`. That is wiring, not behaviour, and a docstring claiming it
proves the order reaches `delivered` would be false. The claim about the order
belongs in a test that runs the real handler — which is where it now is.

---

## A status that refuses everything is a decision hiding inside a bug fix

**2026-08-21.** `undelivered` was closed to every transition but `refunded` and
`disputed`, and reaching it refunded the order automatically. The reasoning in
the file was sound as far as it went — treating a failed handover as recoverable
had let an unattended *sweep* send a second driver for something a person had
written off.

But the fix was aimed at the wrong thing. The fault was that a sweep could act
on it, not that the order could recover. Closing the status also stopped the shop
driving a box over itself, handing a stuck order to a different courier, and
acting on the commonest case there is: a driver reports no answer at the door and
the customer rings back ten minutes later. Hussain's answer was the narrow one —
*add an undelivered → cancelled transition, and auto-refund only on cancelled.*
The status reopens; the money moves to the press that means "this order is over".

**Two pieces of the codebase had already voted.** `email_copy.undelivered.next_body`
says *"Someone from our team will contact you shortly to arrange another
attempt"*, and the template header says the next step is *"a person arranging
another attempt rather than a refund"*. Neither was updated when the refund was
made automatic, so the shop had been emailing a promise the code had stopped
keeping. The copy was not stale — it was the requirement, still sitting there.

**Rule:** when a status is closed off, write down which of its *legitimate* exits
are being closed with it. "A sweep must not do X automatically" and "a person may
never do X" are different sentences, and only the first was ever the problem. The
fix for an unattended actor is to constrain the actor.

**Rule:** before reversing a behaviour, grep the customer-facing copy for what it
currently promises. Copy that contradicts the code is evidence about which of the
two is wrong, and it is usually the newer one — the copy was written when somebody
was thinking about the customer.

**Corollary, and the trap in the reversal:** removing the auto-refund made
`order_undelivered.html` fall through to its `{% elif was_paid %}` branch —
*"we're returning your payment"* — on every order, because no refund is ever
recorded now. The false promise would have printed directly beneath the line
saying somebody will ring to arrange another attempt. **Deleting the cause of a
value does not delete the branch that renders its absence.** Grep for every
reader of a field you have just stopped writing.

## A dedupe that never matches is indistinguishable from no dedupe at all

Same change. `already_sent()` asked `email_logs` for `template == "order_packed.html"`
so a reassignment would not re-send an email the customer already had. The column
holds `order_packed`: `_send_order_email` logs `template.removesuffix(".html")`.

Every test passed. They passed *because* the query matched nothing — the helper
answered "never sent", the email went, and the assertion was that the email went.
The failure mode only appears on the second send, to a real customer, in
production. A guard that is wired backwards fails open and open is the happy path.

**Rule:** when you query a column another function writes, read the writer, not
the model. `template=template` at the call site and `template.removesuffix(...)`
at the write are four lines apart and say different things.

**Rule:** a deduplication test must assert the *suppression*, not the send —
seed the row it should match, then assert nothing went. A test that only proves
the first send works cannot tell a working dedupe from an absent one.

## Copying a row means copying every column, and the compensating code may be dead

`create_version` clones every polygon into a draft map. Its copy list carries
name, fee, pricing mode, both free-delivery fields, provider, branch, geometry,
bbox and display order — each with a comment explaining what breaks if it is
dropped. It does not carry `batch_group_id`, so **publishing a draft silently
stops batching**: the same orders, one van each, at several times the cost.

The block that looks like it compensates is dead. It calls
`_windows_of(db, polygon.id)` against a function that filters
`DeliveryBatchWindow.group_id`, so it always returns empty; and it constructs
`DeliveryBatchWindow(polygon_id=...)`, a column that has not existed since
windows moved onto groups. If it ever did return a row it would raise.

The comment beneath it warns about exactly the outcome the missing line causes.
The warning was written, the assignment was not, and the dead loop made the gap
look filled.

**Rule:** when adding a column to a table something clones, add it to the clone
in the same commit and write the test that publishes a draft and reads the value
back. Every field already in that list is there because somebody found out the
hard way.

**Rule:** a loop that never executes reads exactly like a loop that works. When
a copy path has a compensating block, check its filter actually matches — pass it
a real id and count the rows — before trusting that it covers anything.

## The compensating block is the first place to look, and the last place anyone does

**2026-08-21, the follow-up to the copy-every-column lesson above.** Fixing
`create_version` turned up more than the missing `batch_group_id`.

The loop that appeared to carry the schedule into a draft had **two** independent
faults, either of which alone would have made it useless: it passed a polygon id
to `_windows_of`, which filters on `group_id`, so it always matched nothing; and
it built a `DeliveryBatchWindow(polygon_id=...)` against a column that stopped
existing when windows moved onto groups in `088`. Dead in two ways, next to a
comment that described exactly the outcome its absence caused.

`create_version` had **no test at all**. That is the actual reason this lived:
not that the loop was subtle, but that nothing ever ran it.

**Rule:** a block that exists to compensate for something is worth executing once
by hand before believing it. Pass its filter a real id and count the rows. Two
faults in nine lines is what "nobody has run this since the schema changed" looks
like.

**Rule:** the function that clones a versioned row deserves a test that asserts
*every* column, by name, in a loop. Not the one you just added — all of them. It
is the only shape of test that catches the next person's omission, and this
codebase has now paid for the same omission twice (`free_delivery_threshold`
carries its own comment about it).

**Corollary, on fixes that open doors:** copying `batch_group_id` made a state
reachable that never had been — a draft zone on a run, whose courier an admin can
then change to one the run does not book. `assert_group_fits_polygon` existed for
exactly that and had **no callers**, because nothing could reach the state. When
a fix makes previously-dead validation reachable, wire it up in the same commit;
and when you choose to degrade rather than refuse, check the operator can *see*
what was dropped — a silent detach is the same class of bug as the silent drop
being fixed.
