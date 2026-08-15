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

- [ ] 1. Migration: `out_of_stock_until` on `branch_products`; new
      `branch_modifier_options` mirroring it.
- [ ] 2. `availability_service` — the single definition of "sellable at this
      branch", for one product and in bulk, plus the SQL clause form.
- [ ] 3. Required-group rule: a product with a required modifier group whose
      options are all out is not sellable, and is hidden the same way.
- [ ] 4. Storefront: hide only when out everywhere.
- [ ] 5. Cart: per-branch availability endpoint; order placement refuses an
      unavailable line with a code the client can branch on.
- [ ] 6. POS API: list web products for a branch, set product/option stock with
      a duration.
- [ ] 7. POS apps: availability screen in the kit, on both iPad and iPhone.
- [ ] 8. Web: checkout resolution screen.
- [ ] 9. Admin: surface the same state read-only where availability is listed.
- [ ] 10. Tests, OpenAPI regen, deploy.

## Review

(filled in as work lands)
