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
- [ ] 8. **Web: checkout resolution screen.** The API half is done and the
      contract is generated (`UnavailableItem`); the checkout does not read it
      yet, so today an unavailable basket is stopped at the pay button by the
      409 rather than guided before it.
- [ ] 9. Admin: surface the same state read-only.
- [ ] 10. Deploy.

## Review

**Landed.** The backend end to end, and the terminal screen on both apps.
`branch_products.is_in_stock` finally has a reader, an expiry and a way to be
set from a counter. 1546 API tests and 246 kit tests pass; both simulators were
driven through the screen rather than only built.

**Found on the way.** `is_in_stock` had been dead since migration 038 — a table,
two admin endpoints and a docstring promising an end-of-day restore that nothing
implemented. `pos.products.availability` likewise already existed, which is why
this needed no new role.

**Not done, and why it matters.** Item 8. The server refuses an unavailable
basket, so nothing can be *sold* that a branch cannot make — the guarantee
holds. What is missing is the courtesy: a customer who changes their address at
the checkout and moves the order to a branch without their filling gets a
refusal at the pay button instead of being shown which line to change. Until
that ships, prefer marking items out with `include_options` off so whole
products (which read clearly in the refusal) are the common case.

**Deployment deliberately not run.** The feature is inert until somebody marks
something out — no rows means no behaviour change — but shipping the refusal
without the screen that resolves it is a worse checkout than shipping neither.
