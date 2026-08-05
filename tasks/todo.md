# Melting Moments Ecommerce - Build Tracker

## ⏳ 2026-08-04: Emails in the language the order was placed in

Every email was English, including to a customer who browsed, read the checkout
and paid entirely in Arabic. Nothing on an order recorded a language, so the
mailer had nothing to switch on — the branch card hedged by stacking both.

### Plan
- [x] 1. `071_order_locale` — `orders.locale`, NOT NULL, default `en`. A property
      of the order rather than of the customer: a guest has no account to hang a
      preference on, and the useful question is "what were they reading when they
      placed this", which is known exactly once.
- [x] 2. `normalise_locale` — takes `ar-AE`, `AR`, `fr`, `None` and answers with
      one of the two the shop has copy for. Never raises: an unrecognised locale
      is a reason to write in English, not to refuse a paid order.
- [x] 3. `email_copy.py` — 135 keys × 2 languages, shipped with the templates
      rather than seeded into `ui_translations`. Email copy is welded to the
      template beside it and a missing key lands in an inbox, not on a page
      somebody can refresh.
- [x] 4. One set of templates, `dir`/`lang` off the order, Tajawal/Cairo for
      Arabic, `letter-spacing` and `text-transform` stripped from Arabic labels
      (the script is joined — spacing it pulls the joins apart).
- [x] 5. Bidi: every always-Latin run is an explicit `ltr` island and everything
      that could be either is `auto`.
- [x] 6. Account emails take the locale of the request; the storefront derives it
      from the URL so no caller can forget.

### Review

- **The owner notification stays English.** It is internal, goes to the two
  people who run the shop, and links into an English-only admin. The branch card
  inside it is still the customer's language, because that is the address they
  were shown.
- **Digits stay Western in both.** That is how the storefront writes prices, and
  an order number a customer reads back over the phone has to be the same string
  either way.
- **Two bugs the Arabic render caught that no test would have.** The branch card
  rendered empty — the macro grew arguments its callers never passed — and bidi
  reordered every Latin run inside the RTL text: a phone number showed as
  `1234 552 06`, a quantity line as `AED 95.00 × 2`.
- **One bug the end-to-end run caught that a test was actively hiding**: see
  today's entry in `lessons.md`. `'lang="ar"' in html` matched the stylesheet.
- 886 API tests, 184 web tests, ruff and tsc clean. Migration round-trips on
  PostgreSQL 16, and two real orders — one `ar`, one `en` — were driven through
  `to_response` into the mailer: correct `lang`/`dir`, subject, branch card and
  link language on each.


## ⏳ 2026-08-04: Counter sales send no customer email

Follow-up to the email revamp (PR #18, merged). Website and counter orders share
one table, and the mailer could not tell them apart — so an admin moving a till
sale through the unified orders screen emailed a customer who had been handed
the box across the counter, and put a "New order" in both owners' inboxes.

### Plan
- [x] 1. `email_service.is_counter_sale` — keyed on `source == "cashier"`, the
      column that exists for this distinction and the one the admin's channel
      tab already filters on. Explicitly **not** `is_pos`, which is true for
      website orders too; gating on that would have silenced every customer
      email the shop sends.
- [x] 2. Enforced at the funnel — `_send_order_email` and
      `send_owner_order_notification` — rather than at each caller, because
      `payment_service` calls the senders directly and `notify_status_change`
      is only one of the ways in.
- [x] 3. `OrderResponse.source`, so the mailer can see the channel. Nothing
      customer-facing renders it.
- [x] 4. Tests: every status silent for a counter sale, the senders silent when
      called directly, the owner notification silent, and a regression test
      pinning the gate to `source` rather than `is_pos`.

### Review

- **The `is_pos` trap is the whole story here.** Both an online order and a till
  sale carry `is_pos = True`; only `source` separates them. A test asserts that
  directly, because switching the predicate would break nothing else.
- **Fails open.** A response with no `source` is not treated as a counter sale.
  A stray email to a counter customer is a small cost; silencing the storefront
  is not.
- **Verified on real rows**, not just hand-built models: two orders in a
  migrated database, both `is_pos = True`, one `cashier` and one `online`. The
  counter sale produced nothing; the website order produced the customer
  confirmation and both owner notifications, exactly as before.
- 851 API tests pass, ruff clean.


## ⏳ 2026-08-04: Rebuild the order emails, and give a pickup order a branch

The transactional emails were a serif-on-mauve template from before the
storefront had a design, and they covered four moments out of nine. A pickup
order had no branch the customer chose — `resolve_branch` guessed one — so
neither the email nor the account page could say where to collect from.

### Plan
- [x] 1. **Branch data.** `070_branch_pickup_details` adds `city`, `city_localized`,
      `address_localized` and `offers_pickup`; backfills K001 (Sharjah) and B001
      (Dubai) with address, city and Arabic names. `Branch.name_for` is fixed to
      read the nested `translations` shape the rest of the codebase uses, and
      gains `address_for`, `city_for` and `maps_url`.
- [x] 2. **The customer picks the branch.** Public `GET /branches/pickup-points`;
      `OrderCreate.pickup_branch_id` honoured by `resolve_branch`, so a pickup
      order's `branch_id` is the branch the customer chose. Checkout grows a
      picker showing localized name, address, city and a Google Maps link.
- [x] 3. **One estimate that knows the order.** `fulfilment_service` answers "when
      will this arrive" from the method, the zone's courier and the status —
      sharper once a rider is holding the box. `OrderResponse` gains a branch
      card and a customer-safe fulfilment block (estimate, tracking URL,
      timeline). No courier name, no driver identity: that rule stays.
- [x] 4. **Emails.** New base template on the storefront's palette and type, fluid
      down to 320px, dark-mode aware. Every lifecycle moment covered: confirmed,
      payment failed, packed (pickup and third-party delivery only), out for
      delivery with a live-tracking CTA, delivered, undelivered, cancelled,
      refunded.
- [x] 5. **Storefront.** Account order detail and the guest track page show the
      full timeline, the estimate, the pickup branch with its map link, and the
      tracking CTA when there is one.
- [x] 6. Tests, `ruff`, `tsc`, and every template rendered and eyeballed at desktop
      and mobile widths.

### Review

- **The estimate is one function, not one per surface.** `fulfilment_service`
  answers "when does this arrive" from the method, the courier that serves the
  zone and the order's current status, and the confirmation email, the account
  page and the guest tracking page all render the same answer. They could not
  disagree before because only one of them said anything.
- **Three arrangements, three honest answers.** Collection is prep time, then an
  exact stamp once it is on the counter. A third-party zone is next-day at *day*
  precision and never names an hour, because that is somebody else's van. A
  courier we book is its batch window plus an hour — and once `picked_up_at`
  arrives it becomes that stamp plus 45 minutes, which is the whole point of the
  out-for-delivery email.
- **"Packed" is not always news.** For collection it is come-and-get-it; for a
  third-party zone it is the last thing anybody will ever tell us. For a courier
  we book it means the box is on a shelf, and sending it would demote the email
  that actually matters to second place. `should_send_packed` is where that
  lives, so it is one function to change rather than a condition at each caller.
- **`undelivered` is not a status and had to stop being treated as one.** The
  order stays on `out_for_delivery` because it is still paid for and still ours
  to deliver, so keying the email off status alone would have sent "your order
  is on its way" to somebody who had just watched a driver leave.
- **The customer still is not told who carries their cake.** `OrderDelivery` has
  the provider, the driver and the cost; `Fulfilment` has nowhere to put any of
  them, and a test asserts that. What crosses is a `courier_managed` boolean and
  a link labelled "track live" — the consequence, not the brand.
- **A bug the migration work surfaced:** `Branch.name_for` indexed `translations`
  one level deep and treated the result as a string, but the column holds
  `{locale: {field: value}}` like every other translatable model. It returned
  nothing for every branch that had translations at all. Fixed, with
  `address_for` and `city_for` alongside it.
- **A bug the test suite surfaced:** pydantic coerces a bare `MagicMock` to
  `True` for a `bool` field, so `_order()` in `test_order_service.py` had been
  silently asserting `email_has_account = True` on every order it built. The
  typed `fulfilment` field failed outright rather than quietly, which is how it
  was found; both are now set explicitly.

### Verification

- **API**: 841 tests pass (7 skipped), `ruff check` and `ruff format` clean.
  53 of those are new — the estimate matrix, the email-dispatch rules, branch
  localisation and pickup-branch resolution.
- **Web**: 183 tests pass, `tsc` clean, `eslint` 0 errors (10 warnings, all
  pre-existing and none in touched files). **Admin**: `tsc` clean.
- **Migration**, on a throwaway PostgreSQL 16: chain runs to head, then
  `070 → 069 → 070` round-trips with real branch rows present. K001 and B001
  come back with their Arabic names, cities, pins and `offers_pickup = true`;
  a warehouse row is left untouched and stays closed.
- **End to end against that database**, API running: `GET /branches/pickup-points`
  returns both branches unauthenticated with both locales and their map links,
  and the warehouse excluded. `POST /orders/track` was driven for all three
  arrangements — collection packed (`ready`, exact stamp, branch card, no
  tracking), Lalamove picked up (`on_the_way`, pickup + 45 min, live link,
  `courier_managed: true`) and third party confirmed (`preparing`, tomorrow,
  `day` precision, no link).
- **Emails**: all 15 templates rendered and screenshotted in Chromium at 900px
  and 390px. One fix came out of looking at them — stacking an item row put the
  hairline *inside* an item, which read as two items.

### Not verified here

The storefront pages could not be driven in a browser in this environment: the
Next dev server serves them but the client bundle does not hydrate, so a form
submit falls through to a native GET. It reproduces on an unmodified checkout of
`main`, so it is the sandbox rather than this change — but it does mean the
account order page and the track page have been verified by unit test and by the
API responses behind them, not by eye.

## ⏳ 2026-08-04: One orders screen, and the register learns about website orders

### Plan
- [x] 1. **Phase 2** — merge the three order views into one. `/orders` gains a channel tab (All / Website / Counter), a branch filter, and columns that change with the tab. `/pos-orders` redirects rather than 404s, and leaves the sidebar.
- [x] 2. `GET /orders/admin/all` gains `channel` and `branch_id`; the row model gains `source`, `pos_status`, `order_type`, `branch_id`, `check_number`, `customer_name`, `delivery_fee`. Search now also matches customer name and phone.
- [x] 3. **Phase 3** — push entitlements on both app targets, APNs registration, the waiting-orders queue, the repeating alarm, accept, and the receipt.
- [x] 4. `POST /pos/orders/{id}/accept` — pending → active, idempotent because two cashiers will press it at once.
- [x] 5. Verified: 668 API tests, 142 kit tests, both iOS targets build, ruff and `tsc` clean.

### Review

- **The channel filter is "everything that is not the counter", not "source == online".** Orders placed before the storefront stamped a source are storefront orders, and a filter that quietly hid them would be worse than no filter at all.
- **Push cannot make the sound.** iOS plays a notification sound once and will not loop it, so the payload carries `requires_acknowledgement` and `OrderAlert` owns the tone. It stops for exactly one reason — a person accepted the order or pressed Silence — and deliberately not on a timer, on backgrounding, or on the queue merely being looked at. Three orders take three acknowledgements.
- **Polling is the mechanism; push is the accelerator.** The queue refreshes every 20s regardless, so a terminal with notifications declined or a dead APNs key still gets its orders — and the alarm is re-raised from the poll, which is what makes a missed notification survivable. Built the other way round, a missed push would be a missed order.
- **Per the mm-pos consistency rule**, every decision lives in `MMPos/` — the queue, the alarm, the card, the accept, the print — and each app layer only applies `.incomingOrders(session:)`. The app layers contain no `if` and no prose.
- **Two things worth knowing before this is live:** the Push Notifications capability still has to be enabled on both App IDs in the Apple portal, and there is no `new-order.caf` bundled yet — without it the alarm falls back to a repeating system alert, which works but is not the sound you want.

## ⏳ 2026-08-04: Stripe webhooks were failing silently, and the APNs groundwork

### Plan
- [x] 1. Find out whether Stripe was calling the webhook at all. It was — Cloud Logging shows `POST /api/v1/webhooks/stripe 200` with a `Stripe/1.0` user agent, and an `ERROR` on the same request: **`Stripe webhook error: get`**.
- [x] 2. Root-cause and fix it. Reproduced locally against stripe-python 15.4.
- [x] 3. Fix the second bug — the one that hid the first for three days.
- [x] 4. APNs groundwork: token table, registration endpoints, provider, push service, GitHub secrets.

### Review

- **Root cause.** `stripe.Webhook.construct_event` returns typed resources, and since stripe-python 8 those no longer subclass `dict` — so `event["data"]["object"].get("id")` raises `AttributeError: get`. The SDK is on 15.4. Every `payment_intent.succeeded` threw there. The parse now reads the **verified JSON payload** instead of the SDK object model: `construct_event` still proves the body is authentic, but the fields come out of a shape a minor version bump cannot change.
- **Why nobody noticed.** The route caught every exception and returned 200. Stripe saw an unbroken wall of successful deliveries, never retried, and paid orders sat in `created`. A bad signature is now a 400 and anything else propagates as a 500 so Stripe retries — and because the dedup row is written in the same transaction as the work, a rolled-back attempt leaves nothing behind for the retry to trip over. That second part was a latent bug of its own: with the old code the dedup row committed, so even a retry would have been skipped as a duplicate.
- **Two orders are affected** — `MM-20260803-001` and `MM-20260804-001`, both paid in Stripe, both still `created`. The two 2 Aug sessions never produced a webhook at all, so those customers appear to have abandoned checkout. Once this deploys, resending those two events from the Stripe dashboard replays them properly; no script needed.
- **APNs.** The key is team-scoped and account-wide, so the existing `CWXGV3TWNY` / team `2F94NY8R3T` covers the POS apps too — no new Apple key. JWT signing verified against the real `.p8`. `device_push_tokens` (migration `060`) keys on the token rather than the device, because the token is what Apple addresses and what iOS reissues; dead ones are revoked on `BadDeviceToken`/410 rather than retried forever.
- **The sound cannot come from the push.** iOS will not loop a notification sound, so the push carries `requires_acknowledgement` and the app plays and repeats the tone itself. A push can only ever be the doorbell.


## ⏳ 2026-08-04: Polygons name a branch, and website orders reach its register

### Plan
- [x] 1. Correct the noon Send rate card against the real one. It has a **vehicle tier** (AED 12 bike / 25 bulky car), bands at **10–15** and **15–20** rather than 10–20 and 20+, no band past 20 km at all, and a **+AED 1 surge** during 12:00–15:00 and 19:00–22:00.
- [x] 2. `delivery_polygons.branch_id`, migration `059`, every polygon on every map pointed at K001. `Zone` carries it, `resolve_pickup` prefers it, and the order records which kitchen it belongs to so a later redraw does not move an order that is already baking.
- [x] 3. Website orders land on that branch's register — `is_pos`, `source=online`, `pos_status=pending`, `order_type`, business day, check number, customer name and phone.
- [x] 4. COD orders confirm themselves. Card orders already did, via Stripe's `payment_intent.succeeded`.
- [x] 5. A noon Send zone quotes "within 1 hour"; every other zone promises nothing. Plus a regression test pinning noon Send out of batching.
- [x] 6. Branch selector per zone in the admin. 660 tests pass, ruff and `tsc` clean.

### Review

- **The rate card inverts the case depending on vehicle.** On a bike noon Send beats Lalamove at every distance in range, surge included — 12.45 mean against 22.29 across Sharjah Central, turning a AED 15 fee from a loss into a margin. In the bulky car product at AED 25 they lose at every distance in range. Confirmed with the owner that standard cakes go by bike. Both tiers are implemented so a large cake can be costed honestly rather than silently priced as a bike.
- **The geometry did not change.** Below 15 km the new bands are arithmetically identical to the old ones, and 15 km is the whole zone — so `Sharjah Central` stays a 10 km circle and no map was republished.
- **Verified end to end** on a fresh database: a website order arrives with `branch_id`, `is_pos`, `source=online`, `pos_status=pending`, `order_type=delivery`, business date and sequential check numbers, and `GET /pos/orders?branch_id=…&pos_status=pending` returns it. A cash order reached `confirmed` on its own while a card order stayed `created` awaiting Stripe. Migration `059` fills all 38 polygons across all four maps, and `RESTRICT` refuses to delete a branch with live zones.
- **A bug the end-to-end run caught:** `DeliveryMethodEnum` mixes in `str` but is still an Enum, so `str()` gives `"DeliveryMethodEnum.DELIVERY"` and the comparison against `"delivery"` failed silently — every delivery order reached the register labelled a pickup. Now compared on `.value`, with a test.
- **Deliberate consequence:** `is_pos=True` means POS reports now include website orders. They are real sales at that branch so this is right, but it moves existing numbers — worth looking at the before and after rather than discovering it later.
- **Still to do:** APNs plumbing (blocked on an Apple push key), the unified admin orders screen, and the iPad/phone apps.

## ⏳ 2026-08-04: Move the noon Send outlet code onto the branch

### Plan
- [x] 1. Add `branches.noon_send_outlet_code` (migration `058`), exposed in the branch schemas and editable at Admin → Branches. A courier collects from a *place*, and every other fact about that place — pin, phone, address, whether it takes online orders — already lives on the branch row; Lalamove reads its pickup from there too.
- [x] 2. Make the branch the source of truth. `resolve_pickup` now returns the branch's `reference` and `noon_send_outlet_code` on the shared `PickupPoint`, so both couriers resolve one place once and cannot disagree about it. `NOON_SEND_OUTLET_CODE` survives only as a fallback for a branch with none, which is what keeps the current deployment working with nothing filled in.
- [x] 3. Make `create_task` require `outlet_code` per call rather than reading configuration. A task sent to the wrong kitchen is a rider outside a closed door, and the caller always knows which branch resolved. `is_configured` is now the API key alone — "can we reach noon Send" and "does this branch have somewhere to collect from" are two questions with two answers, and the second is asked per dispatch.
- [x] 4. Rewrite the registration script to work per branch, reading the pin/address/phone off the row and writing the returned code back onto it.

### Review

- **Verified against the real staging API with `NOON_SEND_OUTLET_CODE` deliberately empty**, so only the branch column could supply it. Registered two branches separately — K001 → `PCKP_MLTNGMYRIN`, B001 (Barsha Heights) → `PCKP_TTBSSC1TQX` — dispatched an order, and read the task back from noon Send: `collected from: PCKP_MLTNGMYRIN — Melting Moments Cakes`. That is the multi-outlet case working today, not just designed for.
- A branch with no code refuses with `Branch B001 has no noon Send outlet code — register it and set it on the branch`, named because the fix is a field in the admin and "noon Send is not configured" would send someone to the deploy secrets, which are fine.
- Re-running `--create` on an already-registered branch is refused rather than silently creating a second pickup point — the API has no delete, so duplicates are permanent.
- **What is still single-kitchen:** `resolve_pickup` picks one branch for the whole country. Making it a function of the destination — the zone that priced the order naming the branch that serves it — is the remaining piece, and everything downstream already takes the pickup point as an argument rather than reaching for a global, so it is the only change needed.

## ⏳ 2026-08-04: Route the trial account to noon Send staging, and run it end to end

### Plan
- [x] 1. Make the trial list the only gate, in every environment. Production points `NOON_SEND_ENV` at noon's **staging** fleet — we have no production key — so the previous "apply the list only when `NOON_SEND_ENV == production`" would have opened noon Send to every Sharjah Central customer the moment that was configured. The list now applies unconditionally, and `TRIAL_CUSTOMER_EMAILS` replaces `NOON_SEND_ALLOWED_EMAILS` because it governs two things rather than one.
- [x] 2. Give the trial account free delivery. New `app/services/trial_customer.py` holds the one membership test — signed in **and** on the list, so a guest typing the address gets neither the discount nor noon Send. Threaded through `calculate_fee` and `quote` and both endpoints, so the checkout and the order can never disagree.
- [x] 3. Run the whole thing end to end against the real noon Send staging API, through the application's own dispatch path rather than a hand-rolled script.
- [x] 4. Fix what the end-to-end run found, and cover it. Full suite 633 passing, ruff and format clean.

### Review

- **The end-to-end run**, on a fresh database migrated to head: a pin at Al Majaz resolves to `Sharjah Central`/`noon_send`/AED 15; the trial account is quoted AED 0 while another signed-in customer and a guest at the same address are both quoted AED 15; a `noon_send` order for a non-trial customer falls through to Lalamove; the trial order creates a real staging task (`HG84NNG6XZ6N6PNN`), estimated at AED 12 over 1,966 m. Feeding the status webhooks in as noon Send would POST them walked the order `packed → out_for_delivery → delivered`, a replay was deduplicated, and a late push was rejected by the ordering guard. All five staging tasks opened during testing were cancelled afterwards.
- **A real bug, found only by that run.** `build_task` derived the COD amount from `order.amount_paid`, which walks the `payments` relationship. It is `lazy="selectin"`, so it is loaded whenever the order came out of a query — and every unit test passed — but a lazy load from inside async SQLAlchemy is a `MissingGreenlet`, not a wrong number, and it killed the first real dispatch. The balance is now summed in SQL by `outstanding_balance()` and passed in, and `build_task` reads plain columns only. The regression test uses an order stub with no `payments` attribute at all, so a reintroduction raises rather than passing.
- **noon Send's webhook timestamps are naive UTC.** Confirmed by reading `created_at` off a live task (`10:26:51`) against our own clock (`10:27:21`) — not Gulf local, which would have been four hours out. Documented at the ordering guard, since local time would silently put `delivered_at` four hours late on every order.
- **The thing to remember about this deployment:** a staging task never sends a rider. The trial account's orders have to be delivered by hand, which is the reason those orders are free.

## ⏳ 2026-08-04: Add noon Send as a second courier, and split Sharjah for it

### Plan
- [x] 1. Price noon Send against Lalamove and decide where each one belongs. noon Send is `12 flat to 10 km, +1/km to 20, +1.5/km beyond`; Lalamove is `17 + 0.70/km` once the AED 5 door-to-door is dropped. They cross at **31.25 road km**, past the far edge of Sharjah City — so price never decides the boundary. The binding constraints are that noon Send cannot cross an emirate boundary and caps a run at 15 km. Road distance runs **1.49x** straight line across the sixteen Sharjah areas the live Lalamove rate card was measured over, so 15 road km is a 10 km circle.
- [x] 2. Cut Sharjah into three zones and publish them. `Sharjah Central` (10 km, noon Send, 15 AED), `Sharjah City` (10–25 km, Lalamove, 15 AED), `Sharjah` (beyond 25 km, third party, 50 AED). The builder punches the inner circle out of the city ring so the shapes stay disjoint, and no fee changes anywhere. Migration `057_noon_send_zone` publishes it as a new immutable version; 050 and 055 were repointed at a frozen copy of the old geometry so a rollback still covers the kitchen rather than leaving a hole in it.
- [x] 3. Build the integration: transport, rate card, routing, webhooks, admin. `noon_send_provider` (coordinates ×10⁷, money in fils, `X-API-Key`), `noon_send_service` (rate card, dispatch, cancel, status and rider-tracking webhooks), and a thin `courier_service` that owns the two policies — the production allow-list and the automatic Lalamove fallback. Batching stays Lalamove-only.
- [x] 4. Gate production to the trial customer. On production a `noon_send` zone only reaches noon Send for a signed-in customer whose own email is in `NOON_SEND_ALLOWED_EMAILS` (`h_abbasi97@hotmail.com`); a guest checkout never qualifies and everybody else is carried by Lalamove without noticing. Staging does not consult the list.
- [x] 5. Drop the Lalamove door-to-door charge. `LALAMOVE_SPECIAL_REQUESTS` now defaults to empty in config, `.env.example` and both deploy workflows — the workflows previously forced `DOOR_TO_DOOR` and would have overridden the code default. Saves AED 5 per Lalamove booking.
- [x] 6. Test and verify. 611 tests pass (7 skipped), ruff check and format clean, admin `tsc` clean. Migration chain verified on a throwaway PostgreSQL 16: upgrade → the three-way split is live and no point matches two zones; downgrade → 055's map is active again and Al Qasimia still prices at 15 AED; upgrade again → correct. The full task lifecycle was exercised against the real noon Send staging API through our own client.

### Review

- **The staging pickup point is `PCKP_MLTNGM3W62`** (`is_serviceable: true`). A staging task was created (`HG84NNDB2V43WUS1` → `pending_assignment`) and cancelled (`cancelled`), whole cycle clean.
- Two things the live run corrected that the spec did not say: `create-task` answers `{"status": "successful"}`, which is an acknowledgement rather than a lifecycle state — the real opening status is `pending_assignment`, and storing their ack would have left a word in `courier_status` that no status map recognises. And the task details put our reference and their task number into one composite `order_id` string, so it is not usable as a lookup key.
- noon Send has **no quotation API and no price on any response**, so `cost_total` for one of their tasks is always our own rate-card arithmetic. It is labelled "(est.)" in the admin so nobody reads it as an invoice line.
- The economics: Sharjah Central costs AED 12 against the AED 15 charged where Lalamove cost 19–26. Al Zahia, University City and Al Rahmaniya are all over the 15 km cap and stay Lalamove even though noon Send would be cheaper there too.
- Zone names stay courier-free on purpose — `zone_name` reaches the browser. A test now asserts no zone name contains "noon", "rod", "lalamove" or "courier".

> **Superseded on 2026-08-04** by the merge below. The 15 km cap and the 10 km
> circle in items 1 and 2 came from a rate card that turned out to be wrong; the
> real ceiling is 20 km, so `Sharjah Central` is now 13.4 km and takes Al Zahia
> and University City with it. Migration `057_noon_send_zone` was renumbered to
> `065_sharjah_central_noon_send` and rewritten onto `059`'s map, where the outer
> zones are 80 AED rather than 50.

## ⏳ 2026-08-04: Audit the live courier data against both API specs

Read the two real Lalamove deliveries in production against Lalamove's webhook
docs and noon Send's OpenAPI spec, and walked the order journey for all three
providers looking for the unhappy paths nobody had exercised.

### Findings and fixes
- [x] 1. **Lalamove delivery times were four hours in the future**, on both orders. `data.updatedAt` is Gulf local wearing a `Z`, in the non-time format `HH:MM.ss`; the top-level epoch `timestamp` is the truth and matched our own receipt clock to the second. `webhook_time()` now reads the epoch. The string also parsed only under Python 3.12 — 3.14 raises — so the recorded value would have changed on a runtime upgrade with no test failing.
- [x] 2. **The POS was never told a rider was coming, for either courier.** Lalamove's announcement hung off `DRIVER_ASSIGNED`, an event production has never once received; the driver id arrives on the status change instead. noon Send's status webhook is three fields and carries no rider at all. Both now fetch the details — `GET /v3/orders/{id}/drivers/{driverId}` and `GET /public/v1/tasks/{nr}` — the first time a driver id appears, then announce.
- [x] 3. **noon Send's out-of-order guard was never armed.** It compared a `timestamp` their published webhook contract does not include. Replaced with a status rank, so a late `assigned` cannot displace `delivered`.
- [x] 4. **The partner limits are now asked for, not guessed.** `GET /public/v1/configurations` reports `distance_limit`, `cod_limit` and `prepaid_limit` for our own key. Three sources disagreed about the distance cap — doc 15 km, rate card 20 km, staging key 50 km. `max_distance_m()` takes the stricter of theirs and ours, and `may_serve` refuses an order over the money ceilings instead of letting task creation reject it.
- [x] 5. **A cancelled order stayed open on the register.** Nothing cleared `pos_status`, so the check stayed live on the iPad — production already has cashier orders sitting `cancelled` with `pos_status = active`. Cancelling now voids an open check and leaves a settled one alone.

### Not bugs, checked and cleared
- The two e2e orders show `total` = delivery fee only. Correct: both used the 100% promo `CLAUDETEST100`, so the discounted subtotal really is zero.
- `specialRequests: 5` in both price breakdowns is the door-to-door charge, dropped in this session's earlier work — both orders predate that deploy.
- A batch showing `cancelled` against a delivered order is the empty-batch path working: the order was dispatched directly before the window closed, so the run had nothing left to collect.

### Follow-up, same day
- [x] 6. **Every order names its kitchen.** `orders.branch_id` was nullable and null on every website order, so the column meant "the branch that made this" for a POS order and "nobody knows" for a storefront one. Migration `068` gives the historical rows K001 and closes the column. Creation now resolves the branch *before* the insert — zone's kitchen, then the configured pickup branch, then any active branch — so the row cannot be written without one.
- [x] 7. **noon Send's ceiling is ours, full stop.** `/configurations` reports whatever an environment is set up for and staging answers 50 km, which describes no real fleet. 20 km is the agreed limit, the distance the card prices to, and what `Sharjah Central` is drawn against, so their number is logged when it disagrees and never moves the guard. The COD and prepaid ceilings from the same call are still enforced — those are real.

### Still open
- **Tracking pushes are switched off.** `is_external_tracking_enabled: false` on our key, and the integration doc says live telemetry "will be live soon", so `/webhooks/noon-send/tracking` will not fire yet. The endpoint stays; live position has to be pulled from the task detail until they enable it.
- **The economics are negative on both real orders**: Sharjah City charged 15 against a 23 cost, Dubai City charged 25 against 39. Dropping door-to-door takes those to 18 and 34 — still −3 and −9.
- **No website order has ever reached a register.** Every online order in the database has `branch_id` null; the routing shipped today and is unexercised in production.

## ⏳ 2026-08-04: Merge 26 commits of `origin/main`, and settle the delivery model

Six commits of courier and POS work had been built on a `main` that was 26
commits behind. The remote had since landed dynamic pricing, a `pricing_mode`
flag, a free-delivery scope column, batch retry and a rewritten city map — none
of it visible locally, and three migration numbers collided.

### Plan
- [x] 1. Merge `origin/main`. Thirteen conflicted files, resolved by keeping the remote's delivery model whole and layering the branch/courier/push work on top of it: `pricing_mode` + `free_delivery_eligible` + `branch_id` all coexist on a polygon, and `price()` now routes its cost estimate through `courier_service` so a noon Send zone is costed on noon Send's card from noon Send's kitchen.
- [x] 2. Renumber the migration chain to `063`–`067`, rebased on `062_retire_cocoa_butter_phrase`.
- [x] 3. Rebuild the map to the agreed model: `Sharjah Central` 13.4 km / 15 / noon Send / free over threshold, `Sharjah City` to 25 km / 15 / Lalamove / free, `Ajman City` 30 km / 15 / Lalamove / free, `Dubai City` 40 km / 25 / Lalamove / free, everything else 80 / third party / never free. All static, so nothing is refused for want of a quote.
- [x] 4. Fold the arrival estimate together. A zone that books itself but is never batched — noon Send — is an hour from now at `"time"` precision, rather than falling into the third-party "tomorrow" branch. The storefront already renders this field, so "within the hour" needed no new copy.
- [x] 5. Verify. 766 tests pass (7 skipped), admin and web `tsc` clean, and the whole chain runs up → down to `062` → up again on a throwaway PostgreSQL 16 with the seeded map checked in SQL each time.

### Review

- **13.4 km is 20 road km.** noon Send's ceiling is on road distance; the map is drawn in straight lines. 1.49x is the ratio measured across the sixteen Sharjah areas on the live Lalamove rate card, and it is the mean — so a few pins just inside the circle will be over 20 road km. noon Send refuses those and `courier_service` falls back to Lalamove, so the customer's fee never moves either way.
- **Two areas changed hands** against the old 10 km circle: Al Zahia (15.3 road km) and University City (18.7). Al Rahmaniya (23.7) is over the ceiling and stays on Lalamove.
- **A bug the merge introduced and the tests did not catch**: copying a map to a draft carried `pricing_mode`, `free_delivery_eligible` and the batch windows, but not `branch_id`. Publishing that draft would have pointed every zone at nothing and quietly stopped every website order reaching a register. Fixed in `delivery_zones.py`.
- **Nothing about this changes what anyone pays.** noon Send is still gated to the trial account, so every other order in Sharjah Central is carried by Lalamove at the same AED 15 as before.

## ⏳ 2026-08-04: Automatic retry for a run the courier refused

A batch that failed parked in `FAILED` and nothing ever picked it up again — the
sweeper only ever selected `PENDING`. The orders inside sat until somebody opened
the admin batch list and pressed Dispatch. On the 22:30 and 23:00 city windows
that plausibly means overnight.

### Plan
- [x] 1. `delivery_batches` gains `attempt_count` and `next_attempt_at` (migration 060).
- [x] 2. The sweep selects `PENDING` **or** anything with `next_attempt_at <= now`, so
       one column decides what comes back and no status needs special-casing.
- [x] 3. Backoff ladder 5 / 15 / 45 minutes — four attempts in all, then it stops.
- [x] 4. Never retry a terminal failure: no usable address, out of service area,
       courier not configured, no pickup branch. Identical data gets an identical
       answer and only burns courier API calls.
- [x] 5. Never schedule a retry past the pickup kitchen's closing time. A driver
       sent to a shuttered kitchen at 00:05 is worse than one that waits for a human.
- [x] 6. Manual dispatch resets the ladder, so topping up the wallet buys a fresh
       set of automatic attempts.
- [x] 7. Surface attempts and the next retry in the admin batch list.
- [x] 8. Tests, deploy, verify green. PR #14 merged; production run `30885816717`
       green, including the `alembic upgrade head` step that hard-fails the
       deploy — so 060 is applied on the live database. API healthy afterwards,
       and the live quote still prices Sharjah City at 15 with a 13:00 batch
       estimate and Abu Dhabi at 80 next-day.

### Result

- 34 new API tests; 634 pass in total. Migration 060 upgrades, downgrades and
  re-upgrades on a clean PostgreSQL 16 chain, and the partial index lands as
  `btree (next_attempt_at) WHERE next_attempt_at IS NOT NULL`.
- Driven against a real database, not only fakes: of six batches — due, future,
  retry-due, retry-later, given-up, half-booked — the sweep picked up exactly the
  three it should. A refused run then wrote a retry five minutes out, was
  collected when that moment came, and booked on attempt 2 with nothing left
  owing.

### Review

Two defects surfaced while building it, both fixed by the same mechanism:

1. A run that died mid-booking — container restart between the claim and the
   courier's reply — stayed `dispatching` forever. It now carries a retry time
   like any other unfinished attempt.
2. A partly-booked run reported a clean dispatch while the orders in its failed
   second courier order sat in the kitchen. Those come back on the ladder now,
   and `_ready_deliveries` already excludes anything holding a
   `courier_order_id`, so a retry books only the stragglers.

The real-database run caught a third, which the fakes had missed: the sweep
marks every run it claims `dispatching`, including a half-booked one, and the
early returns never put the status back — so a failed retry of a run with a
driver on it would have read as failed, or stuck on `dispatching` once the
ladder ran out. `_fail` now derives that from `courier_order_id`, which is only
ever set by a booking that worked.

Not done, deliberately: no alerting. There is no notification channel in the API
today and inventing one is a larger decision than the retry. A run that
exhausts the ladder shows its error and "Gave up after N attempts" in the admin
batch list, next to the manual button.

## ⏳ 2026-08-04: Locale fallback, mobile viewport bugs, and a sign-in nudge

### Plan
- [x] 1. Audit locale-less URLs and set the fresh-visitor fallback to Arabic. The audit found no 404s — `proxy.ts` already redirects every page route and leaves the asset routes alone — so the only real gap was the fallback, which sent an unrecognised device to English.
- [x] 2. Pin the mobile place-order bar with `sticky` instead of `fixed`, so it stops floating mid-screen.
- [x] 3. Keep the map's search box on screen when its suggestion list opens.
- [x] 4. Stop mobile browsers zooming the page when a form field is focused.
- [x] 5. Offer sign-in rather than sign-up when the order's email is already an account, and save the order's address once there is an account to hang it on.
- [ ] 6. Deploy, verify green, confirm on the live site.

## ⏳ 2026-08-04: Three storefront fixes — map pin, name placeholders, dead promo band

### Plan
- [x] 1. Start the address map on the pin of the address being edited. The pan effect only ever ran when the map instance appeared, so the viewport was whatever `defaultCenter` was at mount and never moved; it now follows the pin, but only when the pin has left the view, so tapping the map does not drag it out from under the finger that tapped.
- [x] 2. Replace the "Fatema"/"Abbasi" name placeholders with generic ones in both locales. The owner's own name read as a pre-filled value rather than a hint.
- [x] 3. Stop a promo band advertising a category the storefront no longer serves. Yesterday's fix taught category *tiles* to disappear with their category; hero slides and promo bands carry a hand-typed `cta_href` with no link to the catalogue, so hiding every dessert left "Straight from the fridge — Shop desserts" filling the home page and pointing at an empty listing.
- [x] 4. Deploy, verify green, confirm on the live site. PR #10 merged; production run `30881396167` green.

### Result

- The address map now opens centred on the address being edited. Verified in a browser against a Ras al-Khaimah pin: the map frames RAK with the marker in view, where before it showed the Dubai default with the marker off the edge.
- Name placeholders read "First name" / "Last name" (`الاسم الأول` / `اسم العائلة`), replacing the owner's own name.
- The desserts promo band is gone from the live home page in both locales — 0 `cat-desserts` links, 0 occurrences of "Straight from the fridge" — while the cookies band is untouched.

### Review

- Yesterday's visibility fix was working: the API had already dropped `cat-desserts` from `/categories` and returned 0 products for it. What survived was a *promo band*, a block type with no category relationship at all — the CTA URL was the only link back to the catalogue, so that is where the rule now lives. Hero slides were given the same guard so the next hidden category cannot reproduce this in the carousel.

## ⏳ 2026-08-03: City courier zones, third-party beyond, and a delivery estimate

### Plan
- [x] 1. Give a polygon an explicit `free_delivery_eligible` flag. Free delivery no longer follows from `pricing_mode`, because the outer zones are about to become fixed-fee too — the property needs a home of its own rather than a proxy.
- [x] 2. Publish a map where the three city zones keep Lalamove, batching, free delivery and their 15/15/25 fees, and every outer zone becomes third-party: flat 80 AED, no free delivery, no batch windows, no courier call.
- [x] 3. Reshape the city schedule around the shop's hours — Batch 1 opens at 23:00 the night before and the last batch closes at 23:00, so the whole 24 hours is covered and nothing dispatches after the store shuts.
- [x] 4. Quote an estimated delivery time from the pin: a batched zone gets its next batch close + 1 hour; a third-party zone gets next-day, whatever the clock says.
- [x] 5. Show it at checkout in both locales, as a date and time where we have one and a date where we do not.
- [x] 6. Expose the free-delivery flag in the admin zone editor.
- [ ] 7. Tests, deploy, verify green, exercise the checkout against a city pin and an outer pin.

## ⏳ 2026-08-03: Free delivery at 150 AED, in the fixed-fee zones only

### Plan
- [x] 1. Drop the threshold to 150 and make it the only one — no second tier at any figure. `058_free_delivery_scope` sets it and `alter_column`s the server default; the model, the service fallback, `@mm/config` and every test fixture now say 150.
- [x] 2. Apply free delivery only where the fee is ours to set. `price()` sets `free_available` for a `static` zone and never for a dynamic one, so a 500 AED order to Abu Dhabi pays the courier's 137 like any other.
- [x] 3. Carry the answer to the storefront. The quote gained `free_delivery_available`; the checkout no longer derives "free" from the subtotal, so it cannot promise what the address will not honour.
- [x] 4. Qualify every customer-facing claim. Banner, checkout upsell, home/about/FAQ copy in both locales, `llms.txt`, `llms-full.txt` and the AI plugin manifest all now say "in selected areas" or name the three city areas outright.
- [x] 5. Deploy, verify green, re-test the checkout against static, dynamic and unserviceable pins. PR #6 merged; production run `30846916357` green. Verified live below.

### Result

- `/delivery/rates` reports `free_threshold: 150`, and it is the only threshold in the system — there is no figure above which delivery is free outside the fixed-fee zones.
- Static zones: 149 AED pays 15, 150 AED pays 0 (`applied: true`), Dubai City at 300 pays 0 against a struck-through 25.
- Dynamic zones: Abu Dhabi at 500 pays 137 and Fujairah at 1000 pays 100, both `free_delivery_available: false`.
- With no pin, a 500 AED basket is `applied: false, available: true` — nothing promised until the address is known.
- Live checkout on a 160 AED basket: Dubai City shows "Free delivery — your order qualifies!" with 25.00 struck through; Abu Dhabi shows "Free delivery isn't available for this address" and charges 137.
- The banner reads "FREE DELIVERY OVER 150 AED IN SELECTED AREAS" in English and the equivalent in Arabic; home, about and both FAQ answers carry the scope in both locales.

## ⏳ 2026-08-03: Lalamove everywhere — static city fees, dynamic fee elsewhere

### Plan
- [x] 1. Add a `pricing_mode` (static | dynamic) to delivery polygons and publish a new map version where every zone is fulfilled by Lalamove: the three city zones keep their static 15/15/25 fees, every other zone prices dynamically from the courier quote. Migration `057_dynamic_delivery_pricing` publishes "Lalamove everywhere v1"; verified on a clean PostgreSQL chain with an upgrade → downgrade → upgrade round trip.
- [x] 2. Price a dynamic area from the live courier quote, rounded up to the nearest whole AED, and treat "no quote" as unserviceable rather than silently falling back to a fee. `delivery_service.price()` is now the single place the decision is made, and both the checkout quote and order creation read it.
- [x] 3. Block order creation for an unserviceable pin and surface it on the checkout page as a clear, actionable section (no courier named). `UnserviceableAreaError` is a 400 carrying its own message; the checkout shows an amber panel under the address with "Change address" and "Collect from store instead".
- [x] 4. Seed batch windows on the new map: five slots for the static city zones (00:00–12:00, 12:00–18:00, 18:00–21:00, 21:00–22:30, 22:30–24:00) and two for every dynamic zone (00:00–17:00, 17:00–24:00). This also fixes a live bug — 055 republished the whole map as new rows without seeding schedules, so nothing has been batched since.
- [x] 5. Make batching cross-polygon: runs closing at the same instant merge into one courier order. `_open_batch` now matches on departure time alone.
- [x] 6. Expose pricing mode in the admin zone editor.
- [x] 7. Tests, deploy, verify green, then exercise checkout against real pins. PR #4 merged; production run `30843656403` green across web, admin and API. Live `/delivery/quote` returns 15/15/25 in the city zones and whole-dirham courier prices beyond them (Al Dhaid 62, RAK 85, Fujairah 100, Khor Fakkan 103, Abu Dhabi 137); Hatta and Liwa Oasis come back `serviceable: false`. The live checkout was driven against all eleven pins in a browser: every fee on screen matched the server's to the dirham, and the two unquotable pins showed the unserviceable panel with the place-order button blocked.

### Result

- Fees beyond the three city zones are now the courier's own price for the pin, rounded up: the flat 50 was under-charging a Khor Fakkan or Abu Dhabi run by 50–90 AED and over-charging a short hop just outside Dubai City.
- Hatta and Liwa Oasis are genuinely unquotable and are now refused at checkout rather than sold at 50 AED and discovered at dispatch.
- Free delivery over 200 AED still applies in the dynamic zones, so a qualifying order to Abu Dhabi now waives a fee that is measured at ~137 AED rather than the assumed 50. Flagged rather than changed — the threshold is deliberately identical everywhere.

## ⏳ 2026-08-03: Enforce hidden website content across catalogue and CMS

### Plan
- [x] 1. Audit public product, category, and CMS-asset queries to identify every path that can expose hidden website content. Product listing, details, featured cards, carts, checkout, headers, category pages, all-products, search, and Home CMS tiles were reviewed. The only CMS asset with a category relationship is the Home category-tile block; banners and promos use standalone URLs.
- [x] 2. Apply one consistent public-visibility rule so hidden products and assets in hidden categories never reach storefront responses. Public API requests now enforce active website products and active categories; direct cart additions and checkout use the same rule; previously-added hidden cart lines are removed; CMS category tiles cannot fall back to a raw href; and server-rendered catalogue fetches are uncached so a hide is immediate.
- [x] 3. Add regression coverage for all-products and CMS assets, review the impact, then test, commit, deploy, and verify production. API lint and 537 tests pass (7 skipped); web lint has only 11 pre-existing warnings, 156 tests and TypeScript checks pass. Commit `74b34a4` deployed successfully in production run `30840645137`; production health and `/en/all-products` return 200, and the public catalogue returns 35 active, web-enabled products in active categories even when inactive query parameters are supplied.

### Review

- The public catalogue is now fail-closed: a product must be active, assigned to the Website channel, and either uncategorised or in an active category. Hiding a category immediately clears its category and featured-product API caches, and storefront renders do not retain a five-minute Next.js response cache.
- Existing cart items that no longer meet that rule are removed on the next cart read; checkout independently rejects them. Category-linked Home CMS tiles are suppressed with their hidden category, while standalone promotional links are preserved.

## ⏳ 2026-08-03: Verify Attibassi Coffee Barsha Heights branch pin

### Plan
- [x] 1. Resolve the owner-provided Maps place link and compare it with live branch `B001`. The canonical pin is `25.0984482, 55.1741736`; the live longitude is incorrectly `55.0807900`.
- [x] 2. Update only `B001` with the approved address and verified coordinates in production. Live `B001` now returns `Attibassi Coffee, Al Shafar Tower 1, Barsha Heights, Dubai` at `25.0984482, 55.1741736`.
- [x] 3. Add a forward-only database migration, test it against a clean PostgreSQL migration chain, commit, push, and confirm production deployment. `056_correct_barsha_heights_pin` and the demo seed are updated; the full API suite and migration upgrade → downgrade → upgrade check pass. Production run `30837237170` succeeded, and the VM reports revision `056_correct_barsha_heights_pin` with the verified `B001` values.

### Review

- `B001` is now the verified Attibassi Coffee location at `25.0984482, 55.1741736`; this is independent of the Sharjah kitchen and does not change delivery polygons.

## ⏳ 2026-08-03: Verify Melting Moments Cakes delivery pin

### Plan
- [x] 1. Resolve the owner-provided Google Maps place URL and record its canonical coordinates. Google resolves it to `25.3304139, 55.3736131`.
- [x] 2. Trace the checkout address/location state and Lalamove booking payload to identify whether either uses a different coordinate. Checkout, contact embed, delivery-zone source, and production branch `K001` used the stale viewport longitude; Lalamove reads its pickup stop from `K001`.
- [x] 3. Set the approved delivery address text to `Melting Moments Cakes, Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah`, add regression coverage, and verify the resulting payload. The live `K001` pickup record now returns the approved address and `25.3304139, 55.3736131`; the migration protects the same values for future deployments.
- [x] 4. Review, test, commit, push, and confirm production deployment. The first hosted migration check caught an obsolete `region_slug` insert after migration 053 removed that column; the corrected migration passed a fresh PostgreSQL upgrade → downgrade → upgrade check, GitHub’s PostgreSQL CI, and production run `30836646686`. The VM reports migration `055_correct_sharjah_kitchen_pin` and a healthy API.

### Result

- The business-place URL’s canonical pin is `25.3304139, 55.3736131`; `55.3710382` was the Google Maps viewport center, approximately 260 metres west of the pin.
- Google Places selection now preserves the owner-approved storefront address for that verified place, while all other places retain their clean Google-formatted address.
- A new immutable delivery-zone version is calculated from the corrected pin, so delivery pricing and the Lalamove pickup record share one source of truth.

### Review

- Production `K001` now returns `Melting Moments Cakes, Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah` with `25.3304139, 55.3736131`; this is the record Lalamove resolves as its pickup stop.

## ⏳ 2026-08-03: Checkout location selection and Lalamove payload audit

### Plan
- [x] 1. Trace the Google Places selection event through the location picker and address form; reproduce the missing map/address update. The map library now emits `gmp-select`, while the app subscribed only to retired `gmp-placeselect`.
- [x] 2. Confirm the current Google Maps autocomplete event contract, implement the smallest compatible fix, and add focused coverage. Convert `event.placePrediction.toPlace()`, then fetch location; tests assert the event name, conversion, map pan, pin callback, and cleanup.
- [x] 3. Audit the remaining Google Maps integration, especially address quality from a selected place versus reverse geocoding; apply any necessary update with coverage. Current Google migration, APIs and browser-key restrictions verified; autocomplete is UAE-restricted and passes a clean selected-place name, street, locality, and country directly to checkout and saved-address forms. Reverse geocoding remains the fallback for a map tap/current location.
- [x] 4. Trace the Lalamove booking payload through checkout and order dispatch; verify coordinates, address lines, recipient name, and phone mapping. Drop stop has formatted lat/lng plus unit number and Address Line 1; recipient has first/last name and E.164 phone.
- [x] 5. Run focused and relevant regression checks, review the impact, commit, and deploy the web fix. Focused picker coverage, 153 web tests, 24 admin tests, 532 API tests, and type-check pass; lint has 11 existing warnings and none in changed code. Production run 30834203611 passed, and the live checkout confirmed autocomplete pans and pins the map while filling `Melting Moments Cakes, 21 Arab Club Street, Al Majaz 3, United Arab Emirates` without Maps console errors.

### Result

- The picker uses the current Google Places `gmp-select` event and converts the event prediction to a Place before fetching its location. A selected result now always pans and sets the marker, then supplies the address directly to checkout and saved-address forms.
- Google Maps JavaScript, Places, and Geocoding APIs are enabled in `melting-moments-cakes`; the browser key is referrer-restricted to the production apex, subdomains, and Vercel hostname, while retaining those API restrictions.
- English checkout addresses retain the useful Google street and locality details while removing repeated mixed Arabic/English fragments. Map taps and current-location use reverse geocoding as their fallback.


## ✅ 2026-08-03: SEO / GEO audit + content rewrite — DONE

### What the audit found

**Already good, left alone.** `robots.ts` allows every AI crawler (GPTBot, PerplexityBot,
ClaudeBot, CCBot…) and points at both sitemaps. `sitemap.ts` covers static routes, categories,
products and blog posts per locale with language alternates. `image-sitemap.xml` exists.
`llms.txt` / `llms-full.txt` routes exist — rare, and a real GEO advantage. Structured data
coverage was already broad: `Bakery`, `Organization`, `WebSite`+`SearchAction`, `Menu`,
`Product`+`Offer`+`shippingDetails`+`hasMerchantReturnPolicy`, `CollectionPage`+`ItemList`,
`FAQPage`, `Article`, `BreadcrumbList` everywhere. Product canonicals already resolve to the
real category slug.

**The problems.**

1. **The copy read as machine-written.** "Artisanal" was in the page title, the manifest,
   `llms.txt`, the OpenSearch description and the image alt text. Alongside it: "handcrafted",
   "bespoke", "indulge", "the finest ingredients", "a genuine obsession with quality", and
   "Every bite tells a story of passion, craft, and a deep love for bringing joy through food".
   Two of the three blog posts were titled around the same vocabulary.
2. **The facts contradicted each other.** `llms.txt`, `llms-full.txt`, `ai-plugin.json` and the
   home-page `Bakery` schema all advertised cash on delivery. The FAQ said cash on delivery was
   unavailable. The checkout only offers cash on *pickup* (`paymentOptionsFor` in
   `checkout/page.tsx`). An answer engine reading this site would state the wrong thing
   confidently — worse than saying nothing.
3. **Almost no commercial keywords.** The copy never used the words people type: *brownie
   delivery Dubai*, *dessert delivery Sharjah*, *birthday cake*, *eggless*, *home bakery*,
   *corporate gifting*, *Eid / Ramadan boxes*, *same-day*, *halal*.
4. **`areaServed: 'AE'`** — one country string. No emirate or city appeared as structured data
   anywhere, for a business whose entire proposition is UAE-wide delivery.
5. **The entity was split three ways.** Home, contact and about each declared their own business
   node with no shared `@id`, so the phone number on one page and the opening hours on another
   never joined up.
6. **No `x-default` hreflang** on any page.
7. **The Open Graph image was an 800×800 logo.** `summary_large_image` and Facebook both want
   1200×630, so shared links were cropped or dropped.
8. **Alt text was generic** — "Artisanal desserts", "Handcrafted cookies", "Melting Moments
   treats". No product, no place.
9. **The FAQ had 8 entries**, none of them the high-intent questions that feed People-Also-Ask
   and answer engines.
10. **Delivery pricing in the FAQ was stale** — a hardcoded AED 35 / AED 50 table, while pricing
    has since moved to versioned polygons quoted at checkout.

### What changed

**Content — migration `054_seo_content_refresh.py`.** Reversible: the previous content of every
row it touches is copied into `seo_content_backup_054` on upgrade, and the downgrade restores it
verbatim and drops the table. It overwrites rather than merges, which is the point — the stored
copy is what was being replaced.

- **About** rewritten end to end, both locales. Was three paragraphs of "passion, craft, and a
  deep love for bringing joy through food". Now says what happened: baking for family, about
  forty trays to get the brownie right, a home kitchen in Sharjah, delivery to all seven
  emirates. First person, contractions, specifics.
- **FAQ** grown 8 → 16. The eight new ones are the queries that actually get typed: same-day
  delivery, how much delivery costs, birthday cakes, corporate/bulk, eggless, "what is a cookie
  melt", where the bakery is, and Dubai specifically. Delivery pricing now points at the live
  checkout quote instead of the stale fee table.
- **Payment facts corrected everywhere** — card online, cash on pickup only.
- **Home** `seo.title`/`description`, the category and cater subtitles, and the USP strip now
  carry the delivery and city terms without reading like a keyword list. The hero slides and
  promo bands from 049 were already good and were left alone.
- **Blog**: the three existing posts keep their slugs (the only URLs here with any age) and get
  new words. Four new posts target real queries — dessert delivery in Dubai, Eid and Ramadan
  boxes, eggless baking, and birthday/corporate ordering. Full Arabic bodies for all seven,
  where the Arabic used to be a three-line stub. Every post now has a cover image.

**Code**

- `lib/schema.ts` grew from 4 constants to the shared entity: one `BUSINESS_ID` every page points
  at, `areaServed` as nine named areas instead of `'AE'`, `founder`, `contactPoint`, `knowsAbout`,
  `OG_IMAGE`.
- Home, contact and about JSON-LD now resolve to that one entity. About gained `AboutPage`,
  contact gained `ContactPage`, home gained `OrderAction` and a `WebSite`→`publisher` link.
- `x-default` hreflang on all eight page types that set language alternates.
- `llms.txt` / `llms-full.txt` rewritten: correct payment facts, service-area list, lead times,
  and a "Quick answers" block written for extraction.
- New `app/opengraph-image.tsx` — 1200×630, generated at build time by `next/og`. Verified: real
  PNG, correct dimensions, renders as designed. Because a page that returns its own `openGraph`
  object replaces the inherited one, images included, the pages that do that now pass `OG_IMAGE`
  explicitly; product, category and blog-post pages keep their real photographs.
- Footer gained a service-area line on every page, behind a new `footer.service_area` i18n key,
  hidden until the seed has run so it never prints the raw key.
- Alt text pass on the about page and the home kitchen gallery.

### Verification

- `pnpm --filter web test` — 17 files, 153 tests, all passing
- `pnpm --filter web lint` — 0 errors (13 pre-existing warnings, none in touched files)
- `pnpm --filter web exec tsc --noEmit` — clean
- `next build` — compiles, 51 static pages generated, `/opengraph-image` emitted
- OG PNG inspected on disk: 1200×630, valid signature, renders correctly
- Rendered HTML checked against a running production server: `og:image`, `og:image:width/height`,
  `twitter:image` and `hreflang="x-default"` all present; home JSON-LD carries the nine
  `areaServed` entries, `knowsAbout`, `founder` and the shared `@id`
- `ruff check` on the migration and `seed_i18n.py` — clean; migration data round-tripped through
  a stub-import harness (`_apply` semantics, blog shape, JSON-serialisability, zero banned words)

### Not done — needs the owner

- **Real photographs.** The OG card is typographic because inventing bakery photos is not
  something I can do honestly. The three biggest remaining image wins need a camera: a second
  photo of Fatema so the About hero and the home baker block are not the same shot; per-category
  tile photos showing one hero product; and product shots for the SKUs whose `image_urls` are
  empty — those are exactly the ones missing from `image-sitemap.xml`.
- **Google Business Profile.** The highest-value local-SEO action for this business is outside
  the codebase: claim the profile, set the service area to the emirates, collect the first
  reviews. `aggregateRating` is deliberately absent from the schema — inventing one is a manual
  action risk, and it can be added for real once reviews exist.

---

## ⏳ 2026-08-03: SEO merge, production deploy, and checkout Maps authentication

### Plan
- [x] 1. Fetch and compare `seo/google-surfaces-audit-fixes` with `origin/main`; identify merge/deploy risks. Clean three-commit merge; a push to `main` triggers Vercel production deployment.
- [x] 2. Verify Google Cloud project access, Maps API enablement, and API-key restrictions without exposing a key. Correct project: `melting-moments-cakes`; required APIs are enabled.
- [x] 3. Trace the checkout pin-location map configuration and apply the smallest production-safe fix. `RefererNotAllowedMapError` was caused by rules that omitted the apex domain and path wildcard; repaired the Google Cloud browser-key allowlist while retaining API target restrictions.
- [x] 4. Run relevant checks and review the production diff/impact. 153 web tests pass, lint has only 13 existing warnings, menu CLI help passes; graph review risk 0.30 with pre-existing coverage gaps.
- [x] 5. Merge the SEO branch into `main`, commit the checkout fix if applicable, push `origin/main`, and confirm the production deployment trigger. Pushed commits `9aae2c2` and `8a2e0b9`; GitHub Actions run 30831576105 completed successfully, including Vercel production build and deploy.
- [x] 6. Switch this workspace to `main` and document verification/results here.

### Result
- Fixed the checkout map without changing application code: the active Maps key in Google Cloud project `melting-moments-cakes` omitted the apex production URL and `/*` wildcards from its browser-referrer allowlist.
- The key now permits `https://meltingmomentscakes.com/*`, `https://*.meltingmomentscakes.com/*`, and the existing Vercel hostname, while retaining restrictions to Maps JavaScript, Places, and Geocoding APIs.
- Browser-tested the live customer path through cart → checkout → Add delivery address: the map loaded successfully and the browser had no Maps errors.

## ✅ 2026-08-03: Image Delivery — measured audit + optimisation — DONE

### What was measured (live, not guessed)

**Product images — 39 SKUs, one image each, all on `storage.googleapis.com/mm-product-images/menu/`**
- Every one is a **2048×1365 JPEG**, avg **362 KB**, **13.79 MB** for the catalogue.
- They *are* going through `next/image` with a correct `sizes`, and a warm Vercel edge
  returns **18 KB AVIF at w=640 in 0.41 s** — that path is healthy.
- The cost is the **cold transform**: a variant nobody has requested yet took
  **3.09 s TTFB** (measured, WebP w=640). Vercel must pull the 362 KB original from GCS
  and encode it before the first byte reaches the browser. 8 srcset widths × 2 formats
  × 39 images = 624 variants, so cold misses are routine on this traffic volume.
- The assumption going in was that shrinking the source would kill that latency. It
  did not — see Findings. It shrinks the bytes, and the fallback if optimisation is
  ever bypassed, but the wait is the optimiser's encode, not its read.

**Local banner artwork in `apps/web/public` — 4.3 MB, and mostly bypassing the optimiser**
- `HeroCarousel` and `PromoBanners` deliberately use raw `<picture>`/`<img>` (art-directed
  mobile crops that `next/image` cannot express). Correct call — but that also means
  **zero compression, zero format negotiation**: plain JPEG at full weight.
- Three hero slides load `eager` above the fold: **648 KB desktop / 560 KB mobile**.
- `person_shot_3.png` is **361 KB for a 514×434 image**. `person_shot_2.png` is 310 KB.
  These are PNGs holding photographs.

**Re-encode benchmark (sharp 0.35.3 / libvips 8.18.3, run on the real files)**

| Asset | Now | After |
|---|---|---|
| Product source 2048px | 460 KB | **92 KB** AVIF @1400px (−80%) |
| Hero banner | 232 KB | **52 KB** AVIF @1920px (−78%) |
| `person_shot_3.png` | 361 KB | **10 KB** AVIF (−97%) |

### Plan
- [x] 1. Re-encode the GCS product sources to 1400px max, mozjpeg q78 progressive.
      Originals copied to `gs://mm-product-images/_originals-2026-08-02/` first.
- [x] 2. `apps/web/scripts/optimize-images.mjs` generates `public/images` from the
      pristine artwork now kept in `apps/web/image-src`.
- [x] 3. `BannerPicture` gives the hero and promo bands AVIF → WebP → JPEG per
      breakpoint, keeping the art-directed mobile crop.
- [x] 4. `next.config.ts`: declare `qualities: [75]`.
- [x] ~~5. Preload the LCP hero.~~ Dropped — the hero `<img>` is already in the
      server-rendered HTML with `fetchPriority="high"`, so a preload link is
      redundant.
- [x] 6. `app/core/images.py` re-encodes admin uploads before they reach R2.
- [x] 7. Verified: builds, 153 web tests, 400 API tests, ruff, live re-measurement.
- [x] 8. Warming turned out to be the change that actually fixed the reported
      slowness. It started as a script and is now `image_warm_service`, fired as a
      background task from the three places an image URL enters the catalogue:
      admin upload, Foodics bulk import, and a CMS content change. Artwork
      generation runs from the `web` build rather than by hand.

### Findings / Result

- **The slow thing was never the byte size — it was the cold transform.** Product
  images were already going through `next/image` with a correct `sizes`, and a warm
  edge served 18 KB of AVIF in 0.41 s. But `/_next/image` produces a derivative on
  the *first* request for each (url, width, quality, format), and that first request
  measured **0.5 s – 9.8 s to first byte**. On ~430 visitors/month spread over 39
  products × 7 widths, a large share of real page views were paying that.
- **Shrinking the sources did not fix the latency.** 2048px/460 KB → 1400px/157 KB
  across the bucket (32.11 MB → 11.94 MB, −63%), and cold transforms still ranged
  0.5–5.7 s afterwards. The encode cost is dominated by Vercel's optimiser, not by
  how much source it has to read. Worth doing for the bytes, not for the wait.
- **Warming the cache is what fixed it.** 273 requests, 141 s, one-off: every product
  image at every width the layouts can resolve to. Re-measured after —
  **0.13–0.22 s, `x-vercel-cache: HIT`, every one.**
- **A warm you have to remember is a warm that does not happen.** Nothing surfaces
  the cold-transform cost in the console — whoever uploads an image warms it by
  looking at it, so the wait always lands on the next customer instead. It now fires
  from upload, bulk import and CMS save as a fire-and-forget background task, and
  skips `/images/banners/*` because those are served as static AVIF/WebP siblings
  and never touch the optimiser. Capped at 200 images per run so a re-uploaded CSV
  cannot stack full catalogue warms; the truncation is logged, not swallowed.
- **The banners were the real byte problem, and they were invisible.** `HeroCarousel`
  and `PromoBanners` render raw `<picture>` on purpose — the mobile frame is a
  different crop, which is art direction `next/image` cannot express — so nothing
  ever compressed or format-negotiated them. Three hero slides loaded `eager` at
  full JPEG weight above the fold.

  | Homepage banners | Before | After (AVIF) |
  |---|---|---|
  | Mobile | 856 KB | **286 KB** (−67%) |
  | Desktop | 991 KB | **225 KB** (−77%) |

- **Renaming files to save bytes nobody downloads is a trap.** Both `person_shot`
  PNGs are opaque photographs — ~350 KB for a 514px image — and converting them to
  JPEG cut them to 20 KB. But they are `next/image` sources, so a visitor gets AVIF
  either way and the source format only affects what the optimiser decodes. The live
  CMS rows point at those exact `.png` paths, so the rename would have 404'd the
  about page in production to save nothing. Reverted; the generator now guarantees
  the output filename set matches `image-src` exactly.
- **`optimize_image` has to check whether alpha is *used*, not whether it exists.**
  A mode check alone keeps opaque RGBA uploads as PNG and gives up most of the
  saving. Caught by a test, not by reading the code.

## ✅ 2026-08-02: Delivery Batching, Zone Map, and Retiring Regions — DONE
- [x] Cut the served cities out of their emirates so no address is in two zones
- [x] Add per-zone batch windows in Dubai time, seeded 00:00/12:00/18:00/21:00/22:00/23:00
- [x] Assign an order to a run when it is packed; dispatch alone when no window covers it
- [x] Book the run route-optimised, split above fifteen drops, match stops back by coordinate
- [x] Re-derive everything still waiting whenever the schedule changes
- [x] Fire due runs from an in-process sweeper with a Postgres advisory lock
- [x] Make webhooks batch-aware, with proof of delivery matched per customer
- [x] Draw the country in the admin, hover for fee and courier
- [x] Remove the region concept from BE, FE and the database
- [x] Move the free-delivery threshold and pickup fee under Delivery Zones

### Findings / Result
- **Batching is worth more than any pricing decision.** Measured live against production
  AE: five Sharjah drops on one route cost **AED 62 total, 12.40 each**, against AED 125
  to send them separately. Route optimisation alone did AED 12 of that — the same five
  stops quoted 74 unordered and 62 reordered — and it is free.
- **Lalamove reorders the stops.** The reply comes back in route order, not send order,
  so each customer is matched to their stop by coordinate. Position-matching would have
  booked every customer after the first against somebody else's address.
- **The zones overlapped and nobody could see it.** Sharjah City sat inside Sharjah and
  priced correctly only because it was listed first. The served circle is now punched out
  of its emirate as a hole, so the price is a property of where the pin is. A test asserts
  every landmark matches exactly one zone.
- **A window is matched at pack time, not order time.** A run can only carry what has been
  baked; scheduling by placement would build routes around cakes that do not exist yet.
- **No queue in this stack**, so the API sweeps once a minute inside its own lifespan,
  guarded by a Postgres advisory lock and `FOR UPDATE SKIP LOCKED`.
- **Regions were a question with a better answer already on the row.** Dropped `regions`,
  `addresses.region`, `orders.region_id`, `branch_regions` and `delivery_polygons.region_slug`.
  Reporting that grouped by emirate now groups by the zone that priced the order.
- 504 API tests pass. Admin and web typecheck clean with no new lint warnings. Every
  migration runs and reverses on a fresh database.

### Still to do
- **Push the branch.** `feat/lalamove-batching` is committed locally only — the active
  `gh` account is `h-abbasi` and the repo belongs to `hussu97`.
- **Migration ordering.** This chain hangs off 048 as `050 → 051 → 052 → 053`;
  `feat/homepage-visual-refresh` has its own `049` off the same parent. Whichever merges
  second must re-parent, and `alembic upgrade head` refuses to run until one of them does.
- Register the webhook URL and fund the wallet in the Partner Portal.

## ✅ 2026-08-02: Lalamove Courier Integration — DONE
- [x] Read the whole Lalamove v3 API surface, including the webhook deck their docs only link to
- [x] Confirm the UAE really is supported in production, and that sandbox AE is not
- [x] Cut the emirate outlines into zones the fee strategy can actually price
- [x] Publish a new polygon version: Sharjah City 15, Ajman City 15, Dubai City 25, everything else 50
- [x] Add `fulfilment_provider` to `delivery_polygons` so a zone names its own courier
- [x] Build the signed API client — quotations, orders, drivers, cancel, priority fee, cities, webhook config
- [x] Quote the courier at checkout, hide it from the customer, record it against the cart
- [x] Add `order_deliveries`, and `out_for_delivery` / `delivered` to the order lifecycle
- [x] Book on packed, cancel on cancelled, leave third-party zones exactly as they were
- [x] Receive and verify webhooks: signature, idempotency, out-of-order handling
- [x] Admin: a delivery-zone map editor with drafts and rollback, and a fulfilment panel per order
- [x] Verify against production AE with live quotes, and against a real database with real migrations

### Findings / Result
- **The docs are wrong about the UAE.** `GET /v3/cities` with `Market: AE` returns `AE AUH`, `AE DXB`,
  `AE SHJ` on production, and `language` is validated to be exactly `en_AE`. The **sandbox** is the
  genuinely broken half: its AE pricing engine 500s and its wallet is unfunded, so lifecycle work has
  to be done against sandbox HK and validated against production AE.
- **An emirate is not a delivery zone.** Sharjah reaches Khor Fakkan, Dubai reaches Hatta, Ajman owns
  two inland exclaves. Those cost three to six times a city run, and Hatta is refused outright with
  `ERR_OUT_OF_SERVICE_AREA`. Each served emirate is now clipped to the radius its rate card was
  measured over — Sharjah 25 km, Ajman 30 km, Dubai 40 km — and listed ahead of its own outline, so the
  city wins the lookup and the remainder stays third-party at 50.
- **The customer is told nothing.** The storefront quote carries no courier field, and a test asserts
  the response model's exact field set so a future addition fails loudly rather than leaking.
- **A courier failure is never a customer failure.** With no credentials configured, `lalamove` zones
  price and sell identically and dispatch by hand. A refused address, an empty wallet or an outage is
  recorded on the delivery row and surfaced to an admin; the order is never cancelled on the customer's
  behalf because a driver declined.
- **Booking commits immediately.** The wallet debit happens outside our transaction and cannot roll
  back with it, so losing the courier's order id would mean double-booking on the next dispatch.
- Verified live against production AE: Al Majaz 15 charged / 25 cost, Palm Jumeirah 25 / 56, Yas Island
  50 / 116 (third-party, estimate still recorded), Hatta refused and the reason stored, and a basket
  over 200 charged 0 with the 25 cost still captured.
- Every migration runs and reverses cleanly on a fresh database; the downgrade hands the live map back
  to the previous version rather than leaving the storefront with none.
- 468 API tests pass. Admin and web typecheck clean with no new lint warnings.

### Still to do
- Register the webhook URL and fund the wallet in the Partner Portal — neither can be done from code.
- Batching is the real lever and is not built: one multi-stop order carrying ten drops costs about a
  third per delivery of ten separate ones, which is what turns every zone profitable.

## ✅ 2026-06-06: Admin Credential Bootstrap Correction — DONE
- [x] Confirm admin reset gap and capture lesson
- [x] Directly update production DB for immediate admin access
- [x] Verify admin password login behavior
- [x] Remove credential values and hashes from repo docs/migrations

### Findings / Result
- Corrected the bootstrap gap with an out-of-band production DB update.
- Production DB was directly updated and verified: all three rows are active admins with a password set.
- Public API login smoke test returned `200` and `is_admin=true` for all three accounts.

## ✅ 2026-06-06: Admin Order Notifications + Passkey Admin Auth — DONE
- [x] Audit current admin auth, order, and email flows
- [x] Add owner order notification email with full order/item details
- [x] Add admin order deeplink and preserve return URL through admin login
- [x] Add admin user list endpoint/page for active admin users
- [x] Seed/ensure admin users: Fatema, Fahim, Hussain, while keeping `admin@meltingmomentscakes.com` as password-only superadmin
- [x] Add passkey credential storage and WebAuthn registration/login endpoints
- [x] Update admin login: email first, then password-only or password/passkey depending on passkey availability
- [x] Add optional passkey setup for logged-in admin users
- [x] Verify API/admin behavior with focused tests and production-safe checks
- [x] Commit the scoped change with required author

### Findings / Result
- Confirmed order flows now send the customer confirmation plus owner notifications to `fatema_f@hotmail.co.uk` and `fahimakhtarabbasi@gmail.com`; the owner template includes customer, delivery/pickup, notes, item/options, payment, totals, and a direct admin order link.
- Admin order deep links use `/orders/{order_number}` and unauthenticated admin users are redirected through `/login?next=...`, then back to the exact order after password or passkey login.
- Added `admin_passkeys` and short-lived `webauthn_challenges` tables, plus a migration that ensures `admin@meltingmomentscakes.com`, `fatema_f@hotmail.co.uk`, `fahimakhtarabbasi@gmail.com`, and `h_abbasi97@hotmail.com` are active admin users. The superadmin account remains password-only.
- Added admin `/admin-users` visibility and `/security` passkey setup/removal screens. Login is now email-first and offers passkey only when that admin has one.
- Verification: API full suite `211 passed`; admin tests `16 passed`; admin production build passed; API ruff passed with the existing redis-extra warning; admin lint passed with one pre-existing `eslint.config.mjs` warning.

## ✅ 2026-06-05: Email Tracking Deeplink + Deliverability — DONE
- [x] Add signed or parameterized tracking link in order emails for guest and registered customers
- [x] Update tracking page to consume deeplink query params and load the order directly
- [x] Verify email template rendering and tracking page behavior with tests
- [x] Check production email domain/DNS deliverability signals
- [x] Commit the scoped change

### Findings / Result
- Order confirmation email CTA now points to public `/en/track?order_number=...&email=...`, so it works for guest and registered customers without requiring account login.
- `/track` pre-fills and automatically looks up the order when both query params are present; manual lookup still works.
- DNS checks: root DKIM selector `resend._domainkey.meltingmomentscakes.com` exists; `send.meltingmomentscakes.com` has SPF include for Amazon SES and MX to `feedback-smtp.eu-west-1.amazonses.com`; root DMARC is present but relaxed (`p=none`) and has no reporting address.

## ✅ 2026-06-05: Umami Funnel + Guest Order Email Investigation — DONE
- [x] Trace frontend Umami script/config and custom event calls for product → cart → checkout → confirmation
- [x] Verify production Umami proxy/script behavior and recent live payloads
- [x] Audit checkout/order/payment/email code paths for guest order notification timing
- [x] Check production RESEND_API_KEY presence and Resend API health without exposing secrets
- [x] Inspect recent production order/email logs for the guest order
- [x] Patch missing behavior if root cause is in code, then verify and commit
- [x] Document findings and verification results

### Findings / Result
- Umami script is present on production and browser-level validation sent `/en/cart` pageview plus `codex_umami_probe` through `/umami/api/send` → locale redirect → 200. The production Umami read API key returned 403, so dashboard-side reads could not be queried from the VM.
- Custom frontend events previously dropped silently if called before `window.umami` was available; added a short retry queue in `apps/web/lib/analytics.ts`.
- Stripe was posting production webhooks to `/api/v1/webhooks/stripe`, but the app only exposed `/api/v1/payments/webhooks/stripe`; production returned 404, so paid order `MM-20260605-001` stayed `created` and did not send confirmation email.
- Added `/api/v1/webhooks/stripe` compatibility route and an idempotency guard for already-confirmed Stripe success events.
- Directly reconciled paid order `MM-20260605-001`: set status to `confirmed`, set payment id to the Stripe payment intent, and sent the order confirmation email. `email_logs` shows status `sent` with a Resend id.

## ✅ Prompt 1: Project Scaffolding & Monorepo Setup — DONE
- [x] Initialize Turborepo with pnpm workspaces (root package.json, pnpm-workspace.yaml, turbo.json)
- [x] Create Next.js 15 `apps/web` (App Router, TypeScript, Tailwind CSS v4)
  - [x] Tailwind theme: primary=#8a5a64, secondary=#d6acab, tertiary=#dfbdc1, + surface/text/border tokens
  - [x] Fonts: Playfair Display (400, 400i, 600) + Jost (300, 400, 500, 600) via next/font
  - [x] Material Icons import
  - [x] Dark mode: class-based toggle (`@custom-variant dark`)
- [x] Create Next.js 15 `apps/admin` (same Tailwind config)
- [x] Create FastAPI `apps/api`
  - [x] pyproject.toml with all deps (fastapi, sqlalchemy, alembic, stripe, resend, etc.)
  - [x] App structure: api/v1/, models/, schemas/, services/, core/ (config, security, deps), main.py
  - [x] Alembic init (env.py + script.py.mako)
  - [x] .env.example
- [x] Create `packages/ui` with basic component exports
- [x] Create `packages/types` with placeholder types
- [x] Create `packages/config` with shared ESLint, TypeScript configs
- [x] docker-compose.yml with PostgreSQL 16 + healthcheck
- [x] Root .gitignore (Node, Python, IDE, .env)
- [x] Copy branding assets: logos → apps/web/public/images/logos/, photos → apps/web/public/images/photos/
- [x] Create favicon from branding/logos/favicon_logo.jpeg
- [x] **Verify**: `pnpm install` ✅ succeeded (320 packages), both apps build ✅

## ✅ Prompt 2: Database Models & Migrations — DONE
- [x] Base model class (UUIDMixin + TimestampMixin in models/base.py)
- [x] User model (email, hashed_password nullable, first/last name, phone, is_active/admin/guest)
- [x] Address model (user FK, label, full address fields, EmirateEnum, is_default)
- [x] Category model (name, slug unique, description, image_url, display_order, is_active)
- [x] Product model (category FK, name, slug unique, description, base_price, image_urls ARRAY, is_featured)
- [x] ProductVariant model (product FK, name, sku unique, price, stock_quantity)
- [x] Cart + CartItem models (user/session support for guests)
- [x] Order model (order_number "MM-YYYYMMDD-XXX", status enum, delivery/payment fields, address snapshot JSONB)
- [x] OrderItem model (snapshots of product/variant names at order time)
- [x] PromoCode model (percentage/fixed, min_order, max_uses, valid dates)
- [x] Database session setup (async engine + session factory) in core/database.py
- [x] Alembic env.py configured for async (from Prompt 1)
- [x] Initial migration: alembic/versions/001_initial_tables.py (10 tables + 4 enums)
- [x] Seed script (scripts/seed_db.py):
  - [x] 5 categories (Brownies, Cookies, Cookie Melt, Mix Boxes, Desserts)
  - [x] 6/5/3/3/3 products per category with realistic names/prices + variants
  - [x] 1 admin user (admin@meltingmomentscakes.com)
  - [x] 2 promo codes: MM10 (10% off), FREESHIP (free shipping)
- [x] Pydantic schemas for all models (Create, Update, Response) across 7 schema files
- [x] **Verify**: all 10 tables register ✅, all schemas import + instantiate ✅, migration syntax ✅
  - Note: DB test skipped (Docker not running) — run `docker compose up -d postgres && alembic upgrade head && python -m scripts.seed_db` to fully verify

## ✅ Prompt 3: Backend Core - Auth, Config & Middleware — DONE
- [x] Config (pydantic-settings): DATABASE_URL, SECRET_KEY, CORS origins, all service keys, APP_ENV
- [x] Security: JWT (access + reset tokens via python-jose), bcrypt hashing (direct, not passlib)
- [x] Dependencies: get_db, get_current_user, get_current_active_user, get_admin_user, get_optional_user
- [x] Auth API (7 routes):
  - [x] POST /auth/register — creates user + returns JWT
  - [x] POST /auth/login — email/password, returns JWT
  - [x] POST /auth/guest — creates guest user, returns JWT
  - [x] GET /auth/me, PUT /auth/me — profile read/update
  - [x] POST /auth/forgot-password — stateless JWT reset token (email stub for Prompt 7)
  - [x] POST /auth/reset-password — validates reset token, updates password
- [x] Middleware: CORS, RequestIDMiddleware (X-Request-ID), LoggingMiddleware (method/path/status/ms)
- [x] Main app: 12 routes, /api/v1 prefix, startup/shutdown events, /health endpoint
- [x] Error handling: 6 custom exceptions (NotFound/BadRequest/Unauthorized/Forbidden/Conflict/Unprocessable) + global handlers
- [x] **Verify**: all 7 auth routes present ✅, JWT encode/decode ✅, bcrypt hash/verify ✅
  - Note: Swagger UI verify deferred until Docker is up (`uvicorn app.main:app --reload`)

## ✅ Prompt 4: Backend API - Products, Cart & Categories — DONE
- [x] Categories API: GET /categories (with product count), GET /{slug}, POST/PUT/DELETE (admin)
- [x] Products API:
  - [x] GET /products (filters: category, search, featured, sort, pagination)
  - [x] GET /products/{slug} (with variants)
  - [x] GET /products/featured
  - [x] POST/PUT/DELETE (admin)
  - [x] Variant CRUD (admin)
- [x] Cart API:
  - [x] GET /cart (by user_id or session_id)
  - [x] POST /cart/items, PUT /cart/items/{id}, DELETE /cart/items/{id}
  - [x] DELETE /cart (clear)
  - [x] POST /cart/merge (guest → user after login)
  - [x] Calculate subtotal, item count, check stock
- [x] Service layer: product_service, cart_service, category_service
- [x] Image upload API: POST/DELETE /uploads/image (Cloudflare R2, validate type/size)
- [x] **Verify**: all 28 routes registered ✅, all modules import cleanly ✅
  - Note: Live Swagger test deferred until Docker is up (`docker compose up -d postgres && uvicorn app.main:app --reload`)

## ✅ Prompt 5: Backend API - Orders, Delivery & Promo Codes — DONE
- [x] Delivery service:
  - [x] Dubai/Sharjah/Ajman → 35 AED, rest of UAE → 50 AED, pickup → free
  - [x] Free shipping if subtotal >= 200 AED
  - [x] GET /delivery/rates, POST /delivery/calculate
- [x] Promo Code API:
  - [x] POST /promo-codes/validate (check active, dates, max_uses, min_order)
  - [x] Admin CRUD
- [x] Orders API:
  - [x] POST /orders (validate stock → calc totals → apply promo → calc delivery → create → clear cart)
  - [x] Order number format: "MM-YYYYMMDD-XXX"
  - [x] GET /orders (user's orders, paginated)
  - [x] GET /orders/{order_number}
  - [x] PUT /orders/{order_number}/status (admin, validated transitions)
  - [x] GET /orders/admin/all (admin, filters by status + search)
- [x] Order service: create_order, update_status, calculate totals (5% VAT baked into prices)
- [x] Address API: GET, POST, PUT, DELETE, PUT /{id}/default
- [x] **Verify**: 47 routes registered ✅, all modules import cleanly ✅
  - Note: Live test deferred until Docker is up

## ✅ Prompt 6: Backend API - Payments (Stripe + Tabby + Tamara) — DONE
- [x] Payment service with provider registry pattern (stripe/tabby/tamara dispatch)
- [x] StripePaymentProvider:
  - [x] Stripe Checkout Session (AED, line items, delivery fee line item, metadata, success/cancel URLs)
  - [x] Cards support (apple_pay auto-enabled via Stripe Dashboard)
  - [x] Discount applied via Stripe Coupon when promo code used
- [x] TabbyPaymentProvider (stub with full TODO docs + API reference)
- [x] TamaraPaymentProvider (stub with full TODO docs + API reference)
- [x] Payments API:
  - [x] POST /payments/create-session (order_number, provider) — idempotent via idempotency_key
  - [x] POST /payments/webhooks/stripe (signature verification, checkout.session.completed/expired)
  - [x] POST /payments/webhooks/tabby (stub, always 200)
  - [x] POST /payments/webhooks/tamara (stub, always 200)
  - [x] GET /payments/{order_number}/status
- [x] Security: Stripe signature verification (HMAC), idempotency keys on session creation, structured audit logging
- [x] **Verify**: 57 total routes registered ✅, all imports clean ✅
  - Note: End-to-end Stripe test requires STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET in .env

## ✅ Prompt 7: Backend API - Email Service — DONE
- [x] Email service (Resend v2): _send base with RESEND_API_KEY guard + error logging, send_order_confirmation/cancelled/packed, send_welcome, send_password_reset
- [x] Jinja2 email templates (mobile-responsive, branded):
  - [x] Base template (logo #8a5a64 header, #d6acab accent, #f9f5f0 bg, footer with links + recipient)
  - [x] order_confirmation.html (items table, totals, promo discount, address/pickup info, CTA)
  - [x] order_cancelled.html (items summary, admin_notes if set, refund notice, shop-again CTA)
  - [x] order_packed.html (delivery vs pickup variant, items summary, address snapshot)
  - [x] welcome.html (feature bullets, shop CTA)
  - [x] password_reset.html (button + plaintext fallback link, 1-hour expiry notice)
- [x] Hook into order status update flow (orders.py PUT /status → BackgroundTasks)
- [x] Use FastAPI BackgroundTasks for non-blocking sends (orders + auth routes)
- [x] Error handling: logs error without raising, RESEND_API_KEY guard skips gracefully
- [x] **Verify**: all 5 templates render ✅ (7100–8000 chars each), imports clean ✅
- [ ] **Verify**: emails send correctly on order status changes

## ✅ Prompt 8: Frontend - Shared UI Components & Layout — DONE
- [x] UI Components:
  - [x] Button (primary/secondary/ghost, sm/md/lg, loading state, fullWidth, forwardRef)
  - [x] Input (text/email/tel/number/password + label/error/helper, forwardRef)
  - [x] Select, Textarea, Badge, Card, Modal, Skeleton, Toast, Spinner, Divider
  - [x] QuantitySelector (- / input / +, min/max, stock-aware)
- [x] Layout Components:
  - [x] Header (sticky, hamburger left, logo center with overlaid text, cart icon right with badge)
  - [x] MobileMenu (slide-in drawer, backdrop, Escape key, nav links, login/signup, close button)
  - [x] Footer ("Made with 100% Love", Instagram + WhatsApp SVG icons, copyright, policy links)
  - [x] PromoBanner (sessionStorage dismiss, "Free Shipping above 200AED · Use code FREESHIP")
- [x] Root layout: Google Fonts via next/font, Tailwind theme, Header + main + Footer, dark mode init script
- [x] API client (apps/web/lib/api.ts): typed fetch wrapper, auth headers, session-id header, error handling
- [x] Cart context (apps/web/lib/cart-context.tsx): state, add/remove/update/clear, optimistic updates, session persistence
- [x] **Verify**: `pnpm build` passes with no type errors ✅

## ✅ Prompt 9: Frontend - Homepage — DONE
- [x] Promo Banner (in layout, from Prompt 8)
- [x] Hero section: 2-col grid (tagline + baker photo with offset border), 3-col action shots below
- [x] Featured products: server-side fetch (5m revalidate), horizontal scroll mobile / 4-col grid desktop, product cards with Add to Cart
- [x] Meet the Baker: full-width bg image (person_shot_4), primary/75 overlay, bordered label, italic quote, READ MORE CTA
- [x] "We Cater To": 6-col occasion grid (Birthdays, Weddings, Corporate, Eid, Ramadan, Celebrations)
- [x] SEO: metadata export, JSON-LD (Organization + WebSite + LocalBusiness), semantic HTML (section/article/h1/h2)
- [x] All images via next/image with alt text, priority on hero, responsive sizes
- [x] **Verify**: `pnpm build` passes cleanly ✅, featured products gracefully handle API unavailability

## ✅ Prompt 10: Frontend - Product Listing Pages — DONE
- [x] Dynamic route: apps/web/app/[category]/page.tsx (Next.js 15 async params)
- [x] Category title (primary, Playfair, uppercase, tracking-widest) + optional description
- [x] Product grid: 1 col mobile, 2 tablet, 3 desktop (gap-6 sm:gap-8)
- [x] Product card: image, name, divider, price, variant selector (when >1 active variant), QuantitySelector, ADD TO CART
- [x] Add to cart: toast success/error, header badge auto-updates via CartContext, out-of-stock overlay
- [x] Server-side data fetching (parallel category + products fetch), notFound() on unknown slug, skeleton loading.tsx
- [x] SEO: generateMetadata (title + description + OG), JSON-LD BreadcrumbList + ItemList
- [x] Empty state: inventory_2 icon + message
- [x] **Verify**: `pnpm build` passes ✅, /[category] renders as dynamic server route

## ✅ Prompt 11: Frontend - Cart Page — DONE
- [x] "MY CART" heading with item count
- [x] Cart items: thumbnail, name+variant, unit price, quantity selector, line total, remove button
- [x] Order summary: subtotal, promo code input + apply, discount line, delivery (calculated at checkout note), total, PROCEED TO CHECKOUT
- [x] Empty cart state: shopping_bag icon + message + Continue Shopping
- [x] Optimistic updates with rollback via CartContext
- [x] Promo code validation via POST /promo-codes/validate, apply/remove flow
- [x] Guest checkout support: creates guest session via POST /auth/guest before redirecting to /checkout
- [x] **Verify**: `pnpm build` passes ✅, /cart renders as static page (116 kB)

## ✅ Prompt 12: Frontend - Checkout Flow — DONE
- [x] Step indicators (1 Information → 2 Delivery → 3 Payment) with completion state
- [x] Step 1 (Information): email, name, phone; address form (saved addresses for auth users); order summary sidebar
- [x] Step 2 (Delivery): radio cards (Delivery 35-50 AED / Pickup free), free delivery threshold note, "orders before 12PM" info
- [x] Step 3 (Payment): totals review, promo code input+apply, payment method (Stripe enabled / Tabby+Tamara coming soon), PAY NOW with total
- [x] Confirmation page: success icon, order number, items, totals, delivery info, Continue Shopping + View Orders CTAs
- [x] sessionStorage persistence across steps, step validation before advancing, back navigation
- [x] Stripe cancel URL resume: detects ?step=payment return, resumes at payment step
- [x] Guest session auto-created before order if not authenticated
- [x] New types: Address, AddressCreate, EmirateEnum, OrderCreate, PaymentSessionResponse
- [x] New API modules: addressesApi, ordersApi, paymentsApi
- [x] **Verify**: `pnpm build` passes ✅ — /checkout (119 kB), /checkout/confirmation (109 kB) both static

## ✅ Prompt 13: Frontend - User Account Pages — DONE
- [x] Login page: email/password, forgot password, signup link, guest checkout link
- [x] Signup page: full form, welcome email trigger
- [x] Forgot password + reset password pages
- [x] Profile page: welcome, quick links (orders/addresses/settings), logout
- [x] Orders page: list with color-coded status badges (created/confirmed/packed/cancelled)
- [x] Order detail: status timeline, items, totals, delivery/payment info
- [x] Addresses page: list, add new, edit/delete, set default
- [x] Settings page: edit profile, change password (via email reset), delete account
- [x] Auth guard: /account/* client-side redirect to /login if unauthenticated
- [x] AuthProvider + useAuth hook (lib/auth-context.tsx)
- [x] Header: account icon → /account or /login based on auth state
- [x] MobileMenu: auth-aware (My Account + Sign Out when logged in)
- [x] authApi.updateMe() added to api client
- [x] **Verify**: `pnpm build` passes ✅ — 15 routes, no type errors

## ✅ Prompt 14: Frontend - Static Pages — DONE
- [x] About Me: hero (person_shot_1.jpg, bg-primary/70 overlay), story sections with offset-border photos, values grid (4 cards), CTA banner
- [x] FAQ: 8-question accordion (client component), numbered with Playfair counters, WhatsApp + Contact CTAs, FAQPage JSON-LD
- [x] Contact: 4 info cards (WhatsApp/Email/Location/Hours), Google Maps embed, contact form (opens WhatsApp with pre-filled message), Instagram + WhatsApp social links
- [x] SEO: metadata export on all pages, JSON-LD (Person/LocalBusiness on About, FAQPage on FAQ, LocalBusiness on Contact)
- [x] **Verify**: `pnpm build` passes ✅ — 18 routes, no type errors

## ✅ Prompt 15: Admin Dashboard - Layout & Product Management — DONE
- [x] Admin layout: collapsible sidebar nav (w-52/w-14), mobile hamburger overlay, top bar (user + logout), admin auth guard
- [x] Admin login: email/password, verify is_admin flag
- [x] Dashboard overview: metrics cards (today's orders/revenue, total products, active promos), recent orders table, quick actions
- [x] Product list: table (image, name/slug, category, price, variants/stock, status badges, actions), search/filter, pagination
- [x] Product create/edit: form (name, auto-slug on create, category, description, price, featured, active), image upload (multi-file, preview with cover badge, move left/right, remove), variants section (add/remove inline, diff on edit)
- [x] Category management: CRUD with inline form, up/down reorder (swap display_order), toggle active, product count
- [x] **Verify**: admin app builds with 9 routes, no type errors ✅

## ✅ Prompt 16: Admin - Orders & Analytics — DONE
- [x] Orders list: table (order#, customer, items, total, status badge, payment, date), debounced search, status filter, pagination, CSV export, row-click to detail
- [x] Order detail: status timeline (Created→Confirmed→Packed), action buttons (Confirm/Pack/Cancel), items table with totals, admin notes
- [x] Promo code management: list, inline create/edit form (code auto-uppercase, type/value/min/max/dates), active toggle, delete with confirm
- [x] Customer list: table (name, email, orders, total spent, joined), debounced search, pagination, "View Orders" button
- [x] Analytics page:
  - [x] Date range quick selectors (7d/30d/90d) + custom date inputs
  - [x] 4 metric cards (revenue, orders, AOV, customers) with growth vs prior period
  - [x] Revenue line chart (Recharts LineChart)
  - [x] Orders bar chart (Recharts BarChart)
  - [x] Top products table (by revenue + quantity)
  - [x] Orders by status pie chart (Recharts PieChart from funnel data)
- [x] Backend analytics API: GET /analytics/overview, /revenue, /orders-chart, /top-products, /funnel
- [x] Backend customers API: GET /users/admin/all with order_count + total_spent subquery
- [x] Sidebar nav: Customers + Analytics links added
- [x] recharts installed
- [x] **Verify**: admin builds ✅ 12 routes, 0 type errors; backend imports clean ✅ 58 routes

## ✅ Prompt 17: Analytics, SEO & Performance — DONE
- [x] Umami docker service + umami-db (PostgreSQL 16) in docker-compose.yml
- [x] lib/analytics.ts: Umami window.umami wrapper for all custom events
- [x] add_to_cart event: fires in ProductCard on successful addItem
- [x] remove_from_cart event: fires in cart page handleRemove
- [x] begin_checkout event: fires in cart page handleProceedToCheckout
- [x] promo_applied event: fires in cart page handleApplyPromo
- [x] order_completed event: fires in checkout/confirmation on order load
- [x] Umami <Script afterInteractive> in root layout (gated on NEXT_PUBLIC_UMAMI_WEBSITE_ID env)
- [x] sitemap.ts: dynamic — static pages + all active categories from API (revalidate 1h)
- [x] robots.ts: allow /, disallow /account/ /checkout/ /cart/
- [x] OG images: metadataBase set in root layout; category pages use category.image_url with logo fallback; Twitter card added
- [x] Breadcrumb component: semantic nav with Home / Category, used in category pages
- [x] CategoryNav: slim desktop bar below header with explicit prefetch={true} on all 5 categories
- [x] Image optimization: next/image already used everywhere; priority on LCP images (header logo)
- [x] Font optimization: next/font with display="swap" already set on both fonts (Prompt 1)
- [x] Custom 404 page (app/not-found.tsx): branded with large display number, Back to Home + Contact CTAs
- [x] Custom error page (app/error.tsx): error boundary with Try Again + Back to Home, error digest shown
- [x] **Verify**: web builds ✅ 20 routes, 0 type errors; sitemap.xml + robots.txt generated as routes

## Prompt 18: Deployment & DevOps
- [ ] Dockerfiles: api (python:3.12-slim, multi-stage, uvicorn 4 workers), web (node:20-alpine, standalone), admin (same)
- [ ] docker-compose.yml (dev): postgres, fastapi hot-reload, web dev, admin dev, umami
- [ ] docker-compose.prod.yml: all services + nginx + certbot SSL
- [ ] Nginx config: reverse proxy (web/admin/api subdomains), SSL, gzip, caching, security headers, rate limiting
- [ ] CI/CD (.github/workflows/): deploy.yml (main push), pr-check.yml (lint/type/test)
- [ ] Environment: .env.example, .env.production.example, per-service env files
- [ ] Scripts: setup.sh, deploy.sh, backup-db.sh, restore-db.sh
- [ ] Health check endpoints for all services
- [ ] **Verify**: `docker compose up` runs full stack, nginx proxies work

## Investigation: Gift Note Card OOS (2026-06-05)
- [x] Review local code paths for product stock, add-to-cart checks, and seed/admin defaults
- [x] Connect read-only to the MM production VM/DB via GCP credentials and inspect the gift note product row
- [x] Compare live DB state with code expectations and identify why the storefront reports OOS
- [x] Document verified root cause, recommended fix, and any follow-up verification

### Review
- Production DB row `gift-note-card` is active but has `is_stock_product=true` and `stock_quantity=0`.
- Public API returns the same values, and storefront code treats `is_stock_product && stock_quantity <= 0` as out of stock.
- This is isolated in production: 1 active stock-tracked OOS product (`Gift Note Card`), 0 active stock-tracked in-stock products, 38 active non-stock-tracked products.
- Likely data/process cause: `stock_quantity` defaults to 0 and is exposed in responses, but product create/update schemas, the admin product form, and import/export do not currently allow setting it. Imports can set `is_stock_product=true` while leaving quantity at 0.
- Recommended immediate data fix: set `gift-note-card.is_stock_product=false` if gift notes should not be inventory-gated, or set a positive `stock_quantity` if physical stock should be tracked.
- Recommended code follow-up: add `stock_quantity` support to product schema/admin/import/export before using `Track Stock` for active products.

## Feature: Product Stock Quantity Admin Support (2026-06-05)
- [x] Update production Gift Note Card row to `stock_quantity=10000`
- [x] Add `stock_quantity` to API product create/update schemas
- [x] Add `stock_quantity` to admin product type and product form, shown when `Track Stock` is enabled
- [x] Add `stock_quantity` to product CSV import/export/template guidance
- [x] Verify schema tests and admin build
- [x] Commit the feature change with the required author

### Review
- Production API now returns `gift-note-card.stock_quantity=10000`.
- `apps/api/tests/schemas/test_product_schemas.py` passed: 8 tests.
- `pnpm --filter admin build` passed.
- `pnpm --filter admin lint` passed with one pre-existing warning in `apps/admin/eslint.config.mjs`.

## Bug: Checkout Cart Empty for Session (2026-06-05)
- [x] Inspect checkout/cart/auth code paths for guest sessions and cart ownership
- [x] Query production DB for `sess_ec3a8be6f7d2423c93d90080e62e77a3` cart and related user/cart rows
- [x] Identify why checkout sees an empty cart after item add
- [x] Patch the root cause and Sentry setup if blocked by app configuration
- [x] Verify with focused tests/build and commit with required author

### Review
- Root cause: `AuthProvider` intentionally hides guest users, so cart/checkout treated an existing guest cookie as unauthenticated and minted a fresh guest user. That overwrote checkout identity and made the backend look at a new empty guest cart.
- Production DB evidence: the supplied session had no cart; the item was on guest user `2145e572-a349-470c-804d-cd09419e2b4a`, while several newer empty guest users were created immediately after.
- Production recovery: created a session cart for `sess_ec3a8be6f7d2423c93d90080e62e77a3` and copied the Gift Note Card line into it.
- Code fix: cart and checkout now call `ensureCheckoutAuth`, which reuses `/auth/me` guest cookies before creating a guest.
- Sentry fix: web/admin browser Sentry now uses `/monitoring` tunnel routes instead of direct `ingest.../envelope` calls.
- Verified: `pnpm --filter web test -- lib/checkout-auth.test.ts lib/api.test.ts` passed; `pnpm --filter web lint` passed with existing warnings; `pnpm --filter admin build` passed.
- Residual: `pnpm --filter web build` compiled and typechecked, then failed because unrelated `/ar/signup` and `/track` prerender attempts exceeded 60s.

## Bug: Website Lists Categories With Nothing To Sell (2026-07-27)
- [x] Trace the categories tab back to `GET /categories` and the storefront product filter
- [x] Count categories against the storefront catalogue (`is_active` AND `is_web_visible`)
- [x] Drop categories whose storefront count is zero from the public listing
- [x] 404 the same categories on `GET /categories/{slug}` so they are not reachable by URL
- [x] Leave the admin view (`include_inactive=true`) counting the full catalogue
- [x] Rename the cache key so stale entries under the old meaning are not served
- [x] Add unit coverage and run ruff + the full API suite

### Review
- Root cause: `category_service.get_all` counted products on `is_active` alone. The
  storefront product query also requires `is_web_visible`, so a category of POS-only
  items (coffee, bottled water imported from Foodics) reported a non-zero count and was
  rendered as a tab that opened onto an empty page.
- Fix: `_countable_products(storefront_only)` builds the join condition once. The
  storefront path adds `is_web_visible` and a `HAVING count(products.id) > 0`; the admin
  path is byte-for-byte the query it was before.
- `get_by_slug` applies the same rule, so a hidden category 404s instead of serving an
  empty page — the argument already made for products in `test_product_channels.py`.
- Cache key `categories:active` -> `categories:storefront`, so no Redis entry written
  under the old semantics survives the deploy.
- Verified: `pytest` 364 passed / 7 skipped; `ruff check` and `ruff format --check` clean;
  generated SQL inspected for both the storefront and admin paths.
