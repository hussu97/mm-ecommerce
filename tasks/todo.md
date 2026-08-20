# Live baskets in the console, and the case for a recovery email

## Goal

Two things the shop cannot currently see:

1. **The baskets that exist right now** — what is in them, who is holding them,
   what they are worth, and how long ago the shopper last touched them.
2. **Whether any of them are reachable** — i.e. whether an abandoned-cart email
   is a thing this shop can actually send, and what it would be worth.

`tasks/conversion-audit.md` already names abandoned-cart recovery as the largest
recoverable revenue line on the site, and names the exact blocker: *"A guest cart
has no email address on it. Email is collected at checkout but never written back
to the cart."* This work removes that blocker and puts the resulting picture on a
screen, so the decision in (2) is made against real numbers rather than a guess.

## Plan

### API

- [x] Migration `116` — `carts.guest_email`, `carts.last_activity_at`
      (+ index, + structural backfill from `updated_at`).
- [x] `Cart` model: the two columns, documented.
- [x] `cart_service`: one `line_unit_price` / `line_total` pair used by both the
      storefront response and the console (money math has one home), a throttled
      `touch()` that stamps `last_activity_at`, and `remember_checkout_email()`.
- [x] Stamp activity on every basket read and write.
- [x] `order_service.preview_order` writes the typed email back to a guest cart.
- [x] `GET /analytics/live-carts` — paginated, filterable, uncached.
- [x] Regenerate `openapi.json` + `@mm/types`.

### Admin

- [x] `/analytics/carts` — the table, on the standard page sizes.
- [x] Sidebar entry, and a longest-prefix fix so a nested route does not light
      up its parent as well.
- [x] Link from the Analytics page.

### Assessment

- [x] `docs/cart-abandonment-email.md` — recommendation, cost, the legal and
      deliverability constraints, and the build sketch.

### Verification

- [x] Unit tests: the stamp, the email write-back, the totals, the endpoint.
- [x] `ruff check` / `ruff format --check` / `pytest`.
- [x] `pnpm --filter admin lint` / `test` / `tsc --noEmit` / `build`.
- [x] `python -m scripts.export_openapi --check`, `@mm/types check:fresh`.

## Review

See `docs/cart-abandonment-email.md` for the assessment. Summary of the change:

- **The console can now see live baskets.** `/analytics/carts` lists every
  basket holding items, newest activity first, with the shopper's email where we
  have one, the goods value, the small-basket surcharge that basket would
  attract, the courier estimate already captured against it, the promo code on
  it, and how long it has been idle. The header counts how many are reachable
  and what they are worth — which is the abandoned-cart business case, measured
  rather than assumed.
- **A guest basket is now reachable.** The email typed into the checkout is
  written back to the cart it belongs to, so a basket abandoned *at* the
  checkout can be followed up. One typed into the form and never submitted was
  previously lost with the tab.
- **One copy of the `ILIKE` escaping.** Adding a search box to the new screen
  would have made a *fifth* byte-identical private `_escape_like`. There is one
  now, in `app/core/search.py`, and the four call sites use it. The pair that
  has to travel together — the escaped pattern and the `ESCAPE` clause that
  makes it mean anything — is a single function, so a call site cannot get one
  without the other.
- **Money is still computed once.** The console's subtotal comes from the same
  `line_total` the storefront's cart response uses, and the surcharge from
  `order_pricing.low_order_fee_for` — no second formula. The promo *code* is
  shown, never a discount: what a coupon is worth is decided at checkout, and a
  figure stored here would be free to drift from it (migration 115).
