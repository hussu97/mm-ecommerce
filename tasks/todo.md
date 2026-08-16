# Role permission audit — consolidate the catalogue and enforce it

## Findings (2026-08-16)

The catalogue in `app/models/role.py` holds **108 slugs**. An AST sweep of all
346 routes in `app/api/v1` plus every service says:

| | count |
|---|---|
| Slugs in the catalogue | 108 |
| Slugs enforced anywhere in `app/` | 47 |
| Slugs enforced nowhere — pure decoration | **61** |
| Routes gated by a `require(...)`/`ensure(...)` permission | 88 |
| Routes gated only by `get_admin_user` (the `is_admin` boolean) | 160 |
| Routes gated only by `get_current_active_user` / `get_optional_user` | 61 |

Three separate problems, and only the first is the one that was noticed:

**1. The catalogue was copied from the Foodics authority matrix wholesale.**
It grants permission over features this business does not have and in several
cases never had: reservations (the tables were dropped in migration
`080_drop_unbuilt_pos`), allergens, timed events, promotions, coupons, price
tags, waiters, table layouts, count sheets, and eleven `x.create` / `x.submit`
pairs in Inventory where the console has one button and one endpoint.

**2. Granularity that never bought anything.** `pos.print.check` and
`pos.print.receipt` are two permissions for pressing print. `reports.*` splits
cost reporting four ways (`cost_analysis`, `menu_cost`,
`cost_adjustment_history`, `inventory_items_cost`) and inventory reporting
three ways, against a POS reports page that shows them together.
`dashboard.general` / `.branches` / `.inventory` gate three panels of one page.

**3. The real hole: the console does not use the catalogue at all.**
`User.can()` returns `True` for any `is_admin` user, and 160 console routes ask
only for `get_admin_user`. So every `admin.*` slug — all 21 of them — is
decorative, and the only thing a role actually restricts today is the register.
There is no way to give someone the branches screen without giving them
payment gateways, staff roles, data export and the audit log as well.

## Decisions

* **Consolidate 108 → 43.** Every surviving slug is enforced on a real route by
  the end of this change. A slug that names no route does not go in.
* **Fold, do not drop, where a right is still exercised.** `pos.products.void`
  becomes part of `pos.orders.void`; `inventory.counts.*`,
  `.quantity_adjustment.*`, `.cost_adjustment.*`, `.production.*` and
  `.spot_check.*` become `inventory.adjustments.manage`. Nobody loses a right
  they were using — the migration maps old grants onto the new slug.
* **Drop outright only what has no feature behind it**: reservations, allergens,
  timed events, price tags, waiters, table layout, count sheets, print.
* **Keep the `is_admin` bypass.** It is the documented "the owner is never
  locked out" escape hatch and `is_super_admin` mirrors it. Changing it would
  silently narrow live accounts, which is a separate decision to take
  deliberately, not a side effect of a catalogue tidy.
* **Gate the console on the catalogue.** Replace `Depends(get_admin_user)` with
  `Depends(require("<slug>"))` on the console routers. `is_admin` users are
  unaffected (`can()` still short-circuits for them); the change is that a
  staff member holding `admin.branches.manage` can now be given the branches
  screen *without* being made an admin, which is the entire point of the
  permission system and has never worked.
* **`admin.users.manage` is delegable, but only downwards.** Handing out the
  role editor would otherwise be handing out every other permission, so rather
  than keeping that one route admin-only, `assert_no_escalation` bounds it: a
  non-admin may compose roles from permissions they already hold, and may not
  mint a super-admin role or set `is_admin` on anyone.
* **The migration rewrites `roles.permissions` in place.** It is a vocabulary
  change to a column, not seed content, and it has to land in the same
  deploy as the code — a `scripts/` file someone forgets leaves every non-super
  role holding 108 slugs the API no longer recognises.

## New catalogue (43)

| Group | Slugs |
|---|---|
| Orders (3) | `orders.read`, `orders.manage`, `orders.custom.manage` |
| Catalogue (3) | `catalogue.manage`, `catalogue.recipes.read`, `catalogue.recipes.manage` |
| Inventory (6) | `inventory.read`, `inventory.manage`, `inventory.adjustments.manage`, `inventory.transfers.manage`, `inventory.purchase_orders.manage`, `inventory.purchase_orders.approve` |
| Customers (1) | `customers.read` |
| Marketing & content (2) | `marketing.manage`, `content.manage` |
| Delivery (1) | `delivery.manage` |
| Reports (4) | `reports.sales`, `reports.cost`, `reports.inventory`, `reports.other` |
| Dashboard (1) | `dashboard.access` |
| Administration (7) | `admin.branches.manage`, `admin.devices.manage`, `admin.settings.manage`, `admin.users.manage`, `admin.payments.manage`, `admin.logs.read`, `admin.data.manage` |
| Cashier app (15) | `pos.register.access`, `pos.discounts.predefined`, `pos.discounts.open`, `pos.charges.open`, `pos.orders.void`, `pos.orders.return`, `pos.orders.split_join`, `pos.orders.edit_others`, `pos.payment.perform`, `pos.payment.refund`, `pos.products.open_price`, `pos.products.availability`, `pos.kitchen.manage`, `pos.till.manage`, `pos.driver.act_as` |

## Plan

- [x] Rewrite `PERMISSION_GROUPS` in `app/models/role.py` to the 43
- [x] Add `PERMISSION_MIGRATION` old→new map next to it (single source for the
      migration and the test that proves no live grant is dropped)
- [x] Remap the 47 enforced call sites in routers/services onto the new slugs
- [x] Gate the console routers on the catalogue (`get_admin_user` → `require`)
- [x] Migration: rewrite `roles.permissions` through the map, both directions
- [x] Tests: catalogue invariants, every slug enforced, migration map total
- [x] Regenerate `packages/types` from the OpenAPI document
- [x] Verify against a throwaway Postgres

## Review

**Catalogue: 108 → 43.** Three slugs came out during the work rather than
before it, because the "every slug is enforced" test refused them and was
right: `catalogue.read` (product, category, modifier and menu-group reads are
the storefront's own public endpoints — a permission in front of them gates
nothing), `customers.manage` (the console lists customers and has never had an
edit endpoint), and `pos.reports.access` (the terminal and the console read the
same report endpoints, already gated per report by `reports.*`).

**Enforcement: 88 → 258 routes.** Coarse `is_admin`-only routes went 221 → 51,
and the 51 that remain are session-scoped rather than authority-scoped: `/me`,
your own till, your own cart and addresses, and the terminal's launch reads
(branches, `pos_config`) that are deliberately open to any signed-in user.

**Two holes found and closed on the way through**, both the same shape as the
refund one already recorded in the catalogue — a right that existed as a slug
and was enforced by nothing:

* `POST /tills/{id}/drawer-operations` asked only "is this your till", not
  "may you take money out of it". A pay-out or a no-sale is the drawer opening
  outside a sale; it now needs `pos.till.manage`. **This narrows access** —
  a cashier who could previously record a pay-out now needs the permission.
  Cash sales are unaffected: they reach the ledger from the payment route.
* Gating the console on `admin.users.manage` would have made that one
  permission worth all forty-three — write a role holding everything, or flip
  `is_super_admin`, assign it to yourself. `assert_no_escalation` in
  `app/core/permissions.py` makes the role editor delegable only downwards: a
  non-admin composes roles out of permissions they already hold, cannot mint a
  super-admin role, and cannot set `is_admin` on anyone.

**The `is_admin` bypass is unchanged.** `User.can()` still short-circuits for
admins, so no live console account loses anything on deploy. What changed is
that a staff member with a role can now be given one console section without
being made an admin, which is what the permission system was for and had never
once done.

**Verified on a throwaway Postgres** (the API suite mocks the DB, so a broken
migration passes every test). Five roles seeded in the old vocabulary:

| Role | before | after |
|---|---|---|
| Owner (super admin) | 0 | 0 — untouched |
| Cashier | 13 | 8 |
| Shift lead | 15 | 8 |
| Stock controller | 20 | 6 |
| Back office | 16 | 6 |

Every resulting slug validates against the new catalogue; `downgrade` puts them
back into slugs the old catalogue accepts. The downgrade is deliberately
generous — a folded slug hands back all of its siblings — because a rollback's
safe failure is the console still working, not a manager locked out mid-service.

**No frontend change was needed.** The role editor renders
`GET /staff/permissions` and had no slug hardcoded anywhere; it picks up the new
groups on its own. The OpenAPI document moved by one docstring, no schema.
