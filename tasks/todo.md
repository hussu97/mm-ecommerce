# Cart add-ons with personalisation, and the free-delivery nudge

## Why

Two changes to the cart page, from the conversion audit
([tasks/conversion-audit.md](conversion-audit.md)):

1. The homepage marquee advertises **"GIFT BOXES WITH A HANDWRITTEN NOTE"** and
   there is no gift-message field anywhere in the codebase. A customer who came
   for the gifting proposition reaches checkout with no way to say who it's from.
2. `free_delivery_threshold` is surfaced on the home banner and at checkout
   (`checkout/page.tsx:520–532`) but the cart page mentions it **zero times**. By
   checkout the basket is decided; the cart is where a second box gets added.

## Design

### The dedup trap this has to be built around

`cart_service._options_key()` keys a cart line on `(product_id, sorted option
counts)`. Two gift notes with different messages produce an identical key, so the
second add merges into the first as `quantity: 2` — **and one of the two messages
is gone**, with no error. The note therefore has to participate in the dedup key.
This is the single most important correctness point in the change.

### Two orthogonal product flags

The ask was a "handwritten note" config, explicitly with "other items as future".
Those are two different questions, so two fields rather than one overloaded flag:

| Field | Question it answers |
|-------|--------------------|
| `is_cart_addon` | Does this show in the cart's add-on tray? |
| `personalisation_type` | Does this capture text, and what kind? |

A future candle or ribbon is a tray item with no text; a future engraved plaque
might capture text without being a tray item. Keeping them separate means neither
future needs a migration. `personalisation_type` is a string, not a boolean, so
the second kind of personalisation is data rather than another column.

Following the `is_customisable` precedent on `Product` — an explicit typed flag
that turns an extra flow on — rather than free-form JSONB, because this config
drives **server-side validation** and must not be shaped like whatever the last
writer put there.

### Deliberately *not* adding a `personalisation_required` column

If `personalisation_type` is set, the note is required. An add-on whose entire
value is the text it carries is worthless empty, and a paid gift note delivered
blank is a customer complaint. One rule, no column, no admin field to get wrong.
If a future add-on genuinely wants optional text, that is a migration made when
we know what it needs.

### A dedicated endpoint for the note

`PUT /cart/items/{id}` takes `quantity` and nothing else. The note is edited by
typing, which means debounced saves — and a debounced save that also carries a
quantity will eventually clobber a quantity change made in the same second. So
the note gets `PUT /cart/items/{id}/note`. One endpoint, one job.

### The nudge may only promise what it can bound to a place

`DeliveryPromiseBanner` establishes the rule and its docstring explains why: a
free-delivery claim is only ever made about a **named zone**, because the
national average ("free over 150") is wrong in every zone — Sharjah is free at
any basket, the far emirates want 200. The cart nudge follows the same rule:
no serviceable area or no zone name → **render nothing**. A stale or borrowed
promise is worse than silence.

No new endpoint — `useLocation()` already supplies `area.free_threshold`,
`free_delivery_available`, `serviceable` and `zone_name`.

---

## Tasks

### 1. Data model — migration `092`
- [ ] `products.is_cart_addon` — bool, default false, not null
- [ ] `products.personalisation_type` — varchar(30), nullable (`handwritten_note`)
- [ ] `products.personalisation_max_length` — int, default 100, not null
- [ ] `cart_items.personalisation_note` — text, nullable
- [ ] `order_items.personalisation_note` — text, nullable. **Separate from
      `kitchen_notes`**, which is a POS field cashiers write — mixing a
      customer's gift text into it is how the wrong thing gets printed
- [ ] Partial index on `products (is_cart_addon) WHERE is_cart_addon`
- [ ] Verify up + down on a throwaway Postgres (per MEMORY: the suite mocks the
      DB, so a broken migration passes every test)

### 2. API
- [ ] `_options_key()` includes the normalised note — different text, different line
- [ ] `CartItemCreate.personalisation_note`
- [ ] `PUT /cart/items/{id}/note`
- [ ] Server validates length against the **product's** `personalisation_max_length`,
      not a constant; rejects a note on a product with no `personalisation_type`
- [ ] `CartItemResponse` exposes `personalisation_note`, `personalisation_type`,
      `personalisation_max_length` so the cart page needs no second fetch
- [ ] `GET /products/cart-addons` — active, website-visible, `is_cart_addon`
- [ ] `_compute_item_totals` snapshots the note onto `OrderItem` and **raises if a
      personalisation product carries an empty note** — the checkout guard
- [ ] Count characters, not bytes: an Arabic note must get the same 100 as English

### 3. Storefront
- [ ] `CartAddonTray` — mini carousel/list on the cart page; hidden when there is
      nothing to show or everything is already in the basket
- [ ] Cart line: textarea when `personalisation_type` is set, with label, live
      `n/max` counter, `maxLength`, debounced save
- [ ] `FreeDeliveryNudge` — progress + remaining, or the qualified state; silent
      when the area is unknown or unnamed
- [ ] i18n EN + AR for every new string; check RTL on the counter and progress bar
- [ ] Analytics: `cart_addon_added`, `personalisation_entered`, and the existing
      `free_delivery_unlocked`

### 4. Admin
- [ ] `ProductForm`: cart add-on toggle, personalisation type, max length
- [ ] Order detail shows the note on the line — somebody has to read it to write it
- [ ] Owner notification email carries it, for the same reason
- [ ] Order confirmation email shows it back to the customer, so a typo is caught
      before it is handwritten

### 5. Obligations
- [ ] **CLAUDE.md §10** — new analytics events mean
      `docs/umami-analytics-setup.md` gets its Custom Events rows **and** a
      Changelog row. Not optional
- [ ] §9 does not apply — no new env vars
- [ ] Tests: dedup separation, length rejection, empty-note checkout block,
      snapshot onto the order, nudge zone rules
- [ ] `ruff check` + `ruff format --check`; both Next apps build and typecheck
- [ ] Commit with `--author="Hussain Abbasi <h_abbasi97@hotmail.com>"`, no
      `Co-Authored-By`

### 5. Register / POS
The note is not a modifier, so `option_snapshot.for_register` will not carry it —
it has to be added to the line payload explicitly.
- [ ] Note travels on the POS order-line payload and renders on the register
- [ ] Appears on the kitchen/packing ticket, so whoever writes it can read it
- [ ] A line whose note fails to decode must degrade to a line **without** a note,
      never to a dropped line — `option_snapshot`'s docstring records what a
      failed decode cost last time: a branch saw an empty queue

## Out of scope (say so rather than silently skip)
- Gift *wrapping* as a distinct concept, and gift-recipient address separation.

## Review

All of the above is done. Two things worth recording.

### The migration id was too long, and only a real database said so

`092_cart_addons_and_personalisation` is 35 characters and
`alembic_version.version_num` is `varchar(32)`. Every DDL statement ran, then
the upgrade failed writing down that it had — leaving a database with the new
columns and no record of the revision.

Nothing in the test suite could have caught it: the suite mocks the database, so
a migration that cannot apply passes all 1290 tests. It was found by running
`alembic upgrade head` against a throwaway Postgres, which is the practice
MEMORY already records for exactly this reason. Renamed to
`092_cart_addons_personalisation` (31).

### `merge` had the same dedup hole as `add_item`

The plan named `_options_key` as the trap and it turned out to have two callers,
not one. `cart_service.merge` folds a guest basket into a user's on sign-in and
compared lines the same way — so a guest who wrote a gift note and *then* logged
in had it silently dropped into an existing line. Fixed alongside, and the new
item carries the note through.

## Verification

- **1290 passed**, 21 skipped (was 1257 at the start of this branch); 23 of the
  new ones are `tests/unit/test_personalisation.py`
- Web: 308 passed across 32 files, including 14 for the nudge's zone rules
- `ruff check` and `ruff format --check` clean across 287 files
- Both Next apps build; both `tsc --noEmit` clean
- Migration `092` applied → downgraded → re-applied on a **throwaway Postgres**,
  with the partial index and all four columns verified present after each
  upgrade and absent after the downgrade
- An Arabic note with a newline round-tripped through the real column and
  counted as 25 characters, confirming characters rather than bytes

## Not done, and why

- **Nothing on the PDP.** A note can only be added from the basket. Adding it at
  the product page is a second entry point to the same field and worth doing,
  but it is not what was asked for.
- The seeded copy assumes one personalisation kind. A second one needs a label
  and placeholder in `seed_i18n.py` and an entry in `PersonalisationField`'s
  `COPY` map — deliberately, so an unlabelled box can never render.
- No gift-note product exists yet. The feature is inert until one is created in
  the admin with **Offer in the cart add-on tray** ticked and **Asks the
  customer to write** set to *Handwritten note*.

---

# The new-customer coupon: applicable at the basket, verified only for delivery

## Why

`NEW` (20% off first 3 orders) carries `requires_phone_verification`, and that
flag was enforced at the wrong moment, in the wrong place, and on the wrong
orders — so the offer was unclaimable by exactly the customer it targets.

- **The basket tray was a guaranteed dead end.** `cart/page.tsx` validated the
  code without sending a phone at all (its own comment said "the basket has no
  phone"), so `is_phone_verified(None)` was always false and the tray always
  drew the red *"Verify your phone number to use this code"* — on a page with no
  phone field and no verification widget anywhere on it.
- **Verification was unreachable for guests.** The only guest-reachable
  `PhoneVerify` sat inside `PromoCodeStep`, collapsed behind an "add promo or
  note" toggle, and rendered only *after* a refusal. The other lived in the
  account address book, behind a sign-in.
- **The gate hit the orders it should not.** It exists because a delivery costs
  a courier that a 20% discount can take past break-even. A collection costs
  nothing — yet a pickup order was refused *harder*, because the only phone
  `create_order` looked at came from a shipping address a pickup does not have.
- **Nothing announced the offer before the basket**, and four machine-readable
  surfaces announced it *wrongly*: `llms.txt`, `llms-full.txt` and
  `ai-plugin.json` each hard-coded "15% off" with no code, while the row said 20%
  with the code `NEW`.

## What changed

- `promo_code_service.validate` gained `delivery_method` and
  `enforce_phone_verification`. It now **reports** an outstanding verification
  (`valid: true`, real discount, `phone_verified: false`) instead of refusing;
  `create_order` is the only caller that enforces, and only for delivery.
  Response carries two orthogonal flags — see `pendingVerification` in `types.ts`.
- Cart: the code applies, the discount shows, and a neutral note replaces the red
  alert. `One offer per customer.` deleted; terms collapse to one line behind
  "Show more".
- Checkout: `PhoneVerify` moved into `AddressModal` under the phone field, where
  the number is typed anyway and a guest can reach it. Place Order is blocked for
  delivery until verified, with a prompt that reopens the address form; the
  server refuses in its own words as a backstop. Pickup is never blocked.
- Homepage: the dead `PromoBanner` component was repurposed into a live strip fed
  from `/promo-codes/featured`, plus a JSON-LD `Offer` on the `Bakery` entity and
  the offer in the meta description. `lib/offer.ts` is the single source all four
  machine-readable surfaces now read.

## Verified

Against a real Postgres and a running API/web (not mocks): cart applies with a
14.00 discount on a 70.00 basket; pickup + coupon + unverified → order created;
delivery + unverified → HTTP 400 in its own words and no row written; delivery +
verified → order created. Homepage strip, JSON-LD `Offer`, both `llms*.txt`,
`ai-plugin.json` and the meta description all carry the live 20% / `NEW`.

**Not verified locally:** the Firebase OTP round trip. There are no Firebase env
vars in this environment and `PhoneVerify` renders `null` without them by design,
so the send/confirm step needs checking on a deploy that has them.

## Follow-ups (flagged, not fixed)

- `promo_code_service.create_bulk` copies `max_uses_per_user`,
  `max_discount_amount` and `first_orders_limit` but **not**
  `requires_phone_verification` — bulk-issued codes silently lose the gate.
- `seed_db.py` seeds no coupon with a `first_orders_limit`, so on a fresh DB
  `advertisable()` returns `None` and neither the tray nor the banner ever
  appears. Confirmed live: `/promo-codes/featured` returned `null` until a row
  was inserted by hand.
- `promo_banner.text` is now an orphaned translation key.
