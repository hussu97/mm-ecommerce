# Branch availability ("86 it") — POS-driven out-of-stock

## Why

A branch runs out of pistachio kunafa. Today the only cure is the catalogue,
which is every branch at once. `branch_products.is_in_stock` was added for
exactly this and never wired to anything — no reader, no expiry, no UI. This
builds the read side, the modifier half, and the two places it has to bite:
the storefront catalogue and the checkout.

## Decisions taken (2026-08-15)

* **Browsing hides a product only when it is out at *every* active branch.**
  The catalogue has no address and therefore no branch; the resolved branch is
  enforced at the cart and again at order placement.
* **The resolution screen covers branch stockouts only.** A de-listed product
  keeps today's silent removal — that is a catalogue change, not a stockout.
* **Ship enabled.** No flag.
* **`pos.products.availability` is the permission.** It already exists and
  already guards the admin endpoint. No new role.

## Model

`is_in_stock` stays the answer to "is it out"; a nullable
`out_of_stock_until` carries when it comes back:

| State | `is_in_stock` | `out_of_stock_until` |
|---|---|---|
| Available | `true` | `null` |
| Out indefinitely | `false` | `null` |
| Out until a moment | `false` | timestamp |

Expiry is evaluated **on read**, so a lapsed row is available again without a
job having run. A sweep tidies rows; it is not what makes the rule true.

"End of the day of operations" resolves against the branch's own
`business_day_start` cutoff, in the shop's timezone — the same rollover
`business_day_service` files orders under.

## Work

- [x] 1. Migration: `out_of_stock_until` on `branch_products`; new
      `branch_modifier_options` mirroring it. — `104_branch_availability_expiry`
- [x] 2. `availability_service` — the single definition of "sellable at this
      branch", for one product and in bulk, plus the SQL clause form.
- [x] 3. Required-group rule: a product with a required modifier group whose
      options are all out is not sellable, and is hidden the same way.
- [x] 4. Storefront: hide only when out everywhere.
- [x] 5. `POST /orders/preview` carries `unavailable_items`; `create_order`
      refuses with `items_unavailable_at_branch`.
- [x] 6. POS API: `/pos/availability` — list, set product, set option.
- [x] 7. POS apps: "Website stock" in the kit, verified on both simulators.
- [x] 8. Web: checkout resolution screen — `items_unavailable` gate state plus
      `UnavailableItems`, which names each line and removes it by product.
- [ ] 9. Admin: surface the same state read-only. Not built; the console can
      still set availability through the endpoint it always had.
- [x] 10. Deployed to production 2026-08-15.

## Review

**Landed.** The backend end to end, and the terminal screen on both apps.
`branch_products.is_in_stock` finally has a reader, an expiry and a way to be
set from a counter. 1546 API tests and 246 kit tests pass; both simulators were
driven through the screen rather than only built.

**Found on the way.** `is_in_stock` had been dead since migration 038 — a table,
two admin endpoints and a docstring promising an end-of-day restore that nothing
implemented. `pos.products.availability` likewise already existed, which is why
this needed no new role.

**Shipped.** Migration `104` ran against production in the deploy's "Deploy to
GCP VM" step; `/pos/availability` answers 401 rather than 404, and
`POST /orders/preview` returns `unavailable_items` on the live API. The
catalogue still lists 35 products and 6 categories, which is the check that
mattered: the hide-everywhere clause is the one thing in here that could have
emptied a storefront.

**Not verified end to end on production.** Marking something out needs a device
token paired against the live register, and the act itself takes a real product
off sale for real customers. Every layer was driven against a stub and against a
local database instead. The first live stockout is worth watching: mark one item
at one branch, confirm the site still lists it (one branch out of several is not
"out everywhere"), then confirm a basket for that branch's address shows the
panel.
