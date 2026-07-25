# Foodics Parity — Feature Map & Gap Analysis

Reference spec for building a **Melting Moments–native replica of Foodics** on the existing
`mm-ecommerce` stack. Foodics is used **only as a functional specification** — nothing here
integrates with, or depends on, Foodics' backend. Third‑party aggregator sync
(Talabat / Noon / Keeta / Deliveroo / Careem) is explicitly **out of scope**.

Sources: live audit of `console.foodics.com` (nav tree, role authority matrix, entity forms,
settings screens) + the public Foodics API reference (`apidocs.foodics.com`) for exact
object shapes and enum values.

---

## 1. Foodics module map (as audited)

| Module | Sub-modules |
|---|---|
| **Dashboard** | General, Branches, Inventory, Kitchen |
| **Orders** | All / Today / Draft / Pending / Active / Ahead / Call Center / API, export |
| **Customers** | profiles, house accounts, loyalty, tags, blacklist, addresses |
| **Reports → Sales** | Sales Reports, Payment Reports |
| **Reports → Inventory** | Inventory Levels, Inventory Control, Inventory History, Purchase Orders, Transfer Orders, Transfers, Purchasing, Cost Adjustment History |
| **Reports → Business** | Taxes, Tips, Gift Cards, Business Days, Shifts, Tills, Drawer Operations, Voids & Returns, Activity Log |
| **Reports → Analysis** | Menu Engineering, Inventory Cost Analysis, Branches Trend, Speed of Service, Product Cost, Modifier Options Cost, Inventory Items Cost |
| **Inventory** | Items, Suppliers, Purchase Orders, Transfer Orders, Inventory Count, Purchasing, Transfers, Production, Quantity Adjustment, Cost Adjustment, Order Transactions, Inventory Categories, Warehouses, Spot Check, Count Sheet |
| **Menu → Builder** | Categories, Products, Modifiers, Combos, Groups |
| **Menu → Settings** | Price Tags, Allergens |
| **Manage** | Users, Roles, Branches, Devices, Taxes & Groups, Payment Methods, Charges, Delivery Zones, Tags, Reasons, Kitchen Flows, Reservations, Online Ordering, Notifications, Settings |
| **Marketing** | Loyalty, Gift Cards, Discounts, Promotions, Timed Events, Coupons |

### Settings surface (audited verbatim)

- **Receipt**: logo, print language (main / localized / both), main + localized language,
  header, footer, invoice title, show order number, show calories, show subtotal,
  show rounding, show closer username, show creator username, show check number,
  hide free modifier options, print customer phone on pickup orders.
- **Kitchen**: kitchen sorting method (as added in cashier | by menu category sort),
  print/show default modifiers on kitchen receipt & KDS.
- **Inventory transactions**: logo, header, footer, *restrict inventory transactions to
  available quantities* (prevent negative stock).
- Also: Cashier App, Display App, Payment Integrations, SMS Providers.

### Reasons (typed)

`void_return` · `quantity_adjustment` · `drawer_operation`

### Devices (typed)

`cashier` · `kds` · `notifier` · `display` · `sub_cashier`

---

## 2. Foodics role authority matrix (verbatim, 136 entries)

This is the definitive capability list and drives our `Role.permissions` design.

**Order**: Read Orders · Manage Orders · Manage Orders Tags

**Customer**: Read Customers · Read Customers Insights · Manage Customers ·
Manage Customers House Account · Manage Customers Loyalty

**Inventory**: Read Inventory Items · Manage Inventory Items · Read Suppliers ·
Manage Suppliers · Create/Submit/Approve/View‑Approved Purchase Orders ·
Create/Submit Transfer Orders · Create Transfers · Send & Receive Transfers ·
Create/Submit Purchasing · Create Purchasing From PO · Create Direct Purchasing ·
Create/Submit Production · Create/Submit Quantity Adjustment ·
Create/Submit Cost Adjustment · Create/Submit Inventory Count ·
Read Order Transactions · Create/Submit Inventory Spot Check · Create Inventory Count Sheets

**Menu**: Read Menu · Manage Menu · Hide Items · Mark Items Out of Stock

**Other**: Read Ingredients · Manage Ingredients · Manage Costs

**Admin**: Manage Branches · Devices · Discounts · Settings · Users · Apps ·
Taxes & Tax Groups · Reservations · Payment Methods · Charges · Tags · Reasons ·
Online Ordering · Notifications · Licenses and Invoices · Allergens · Coupons ·
Gift Cards · Promotions · Timed Events · Delivery Zones · Loyalty ·
Support Credentials · Price Tags · Kitchen Flows · Drivers

**Reports**: Cost Analysis · Inventory Control · Inventory Levels ·
Inventory Transactions · Other Reports · Sales Reports · Cost Adjustment History ·
Menu Cost · Inventory Items Cost · Activity Log · Zatca

**Dashboard**: General · Branches · Inventory · Kitchen

**Cashier & Waiter apps**: Access Cash Register · Access Devices Management ·
Access Reports · Apply Predefined Discounts · Apply Open Discounts · Add Open Charge ·
Join Order · Access Drawer Operations · Perform End of Day · Print Check · Print Receipt ·
Return Order · Split Order · View Done Orders · Void Orders and Products · Void Product ·
Perform Payment · Edit Orders Opened by Other Users · Edit Online Orders ·
Change Table Owner · Send to Kitchen Before Payment · Kitchen Reprint ·
Close Till/Shift With Active Orders · Pay Orders Without Closing ·
Manage Product Availability · Manage Tags on Orders · Apply Ahead Orders · Act as Driver ·
Perform Spot Check · Add Open Price Product · Act as Waiter · Edit Tables Layout ·
Register Users Fingerprint · Edit Products Sent to Kitchen

---

## 3. Canonical enums (from the Foodics API reference)

```
order.type            1 dine_in | 2 pickup | 3 delivery | 4 drive_thru
order.source          1 cashier | 2 api    | 3 call_center          (+ online storefront)
order.status          1 pending | 2 active | 3 declined | 4 closed
                      5 returned | 6 joined | 7 void | 8 draft
order.delivery_status 1 sent_to_kitchen | 2 ready | 3 assigned
                      4 en_route | 5 delivered | 6 closed
order_product.status  1 pending | 2 active | 3 closed | 4 moved
                      5 void | 6 returned | 7 declined
order.discount_type   1 open | 2 predefined | 3 coupon | 4 loyalty | 5 promotion

product.pricing_method   1 fixed (pre-set) | 2 open price
product.selling_method   1 unit | 2 weight
product.costing_method   1 fixed cost | 2 from ingredients

table.status          1 free | 2 occupied | 3 check_printed | 4 reserved

drawer_operation.type 1 pay_in | 2 pay_out | 3 cash_drop
                      4 open_drawer | 5 sales | 6 return

payment_method.type   cash | card | gift_card | house_account | other | third_party

discount.qualification  product | order | both

purchase_order.status 1 draft | 2 pending | 3 approved
                      4 declined | 5 partially_received | 6 closed

inventory_transaction.type  1..12 — purchasing, transfer send, transfer receive,
                      quantity adjustment, return to supplier, production,
                      consumption from production, consumption from orders,
                      return from orders, return from transfers,
                      waste from orders, waste from production

promotion.type        1 basic | 2 advanced
promotion rewards     % off products | fixed off products | % off order
                      | fixed off order | fixed price
promotion conditions  quantity-based | spend-based
```

Key derived fields used throughout: `business_date` (trading day, not calendar day),
`estimated_cash = opening_amount + Σ cash payments − Σ cash returns`,
`variance = closing_amount − estimated_cash`.

---

## 4. What `mm-ecommerce` already has

`apps/api` (FastAPI + SQLAlchemy 2.0 async + Alembic) and `apps/admin` (Next.js 16):

| Domain | Status |
|---|---|
| Categories | ✅ CRUD, translations, ordering, images |
| Products | ✅ CRUD, SKU, barcode, cost, calories, prep time, images, featured, stock qty, `is_sold_by_weight`, `is_stock_product` |
| Modifiers | ✅ Modifier / ModifierOption / ProductModifier with min/max/free/unique options |
| Orders | ⚠️ e‑commerce only — `created/confirmed/packed/cancelled/payment_failed/refunded/disputed`, `delivery|pickup`, single payment, VAT fields, item snapshots |
| Cart | ✅ storefront cart |
| Promo codes | ✅ (≈ Foodics coupons) |
| Regions + delivery settings | ✅ (≈ simplified delivery zones) |
| Users | ⚠️ `is_admin` boolean only — no roles, no PIN, no branch scoping |
| Analytics | ✅ storefront revenue/funnel/top products |
| Audit log | ✅ (≈ Activity Log report) |
| CMS / blog / i18n / email logs / passkeys | ✅ |

## 5. Gap list → what we are building

Everything below is **missing** and in scope.

### 5.1 Foundation
- `branches` (multi‑location), branch business days, branch‑scoped everything
- `taxes` + `tax_groups` (replacing the single flat `vat_rate`)
- `payment_methods` (typed, `auto_open_drawer`)
- `charges` (service charge / delivery charge, auto‑applied, per order type)
- `reasons` (typed: void/return, quantity adjustment, drawer operation)
- `tags` (order / customer / product / revenue center)
- `devices` (cashier / KDS / notifier / display / sub‑cashier) + pairing
- `roles` with the full authority matrix + `staff` users (PIN login, branch scoping)
- `business_settings` (receipt, kitchen, inventory‑transaction settings)

### 5.2 Menu
- `price_tags` + per‑price‑tag product prices
- `menu_groups` (+ product membership)
- `allergens` (+ product links)
- `combos` → sizes → items → options → products
- branch‑level product overrides: price, `is_active`, `is_in_stock`
- product fields: `pricing_method`, `selling_method`, `costing_method`,
  `is_non_revenue`, `name_localized`, `tax_group_id`, `walking_minutes_to_burn_calories`

### 5.3 POS operations
- `sections` + `tables` (floor plan, seats, status, layout meta)
- `tills` (open/close, opening/closing amount, estimated cash, variance)
- `shifts` (clock in/out)
- `drawer_operations` (pay in / pay out / cash drop / open drawer)
- POS order model: `order_type`, `source`, `branch_id`, `table_id`, `device_id`,
  `till_id`, `creator/closer`, `guests`, `kitchen_notes`, `business_date`,
  line‑level status/void/return, **split payments**, tips, rounding, charges, taxes
- order lifecycle: open → add items → send to kitchen → discount/charge →
  split / join → pay (multi‑tender) → close → print; plus void, return, park/draft
- KDS: ticket feed, item bump, station routing (`kitchen_flows`)
- `reservations`
- drivers + delivery dispatch statuses

### 5.4 Inventory
- `inventory_categories`, `inventory_items` (storage/ingredient units + factor,
  min/max/par levels, costing method), `warehouses`, `inventory_levels`
- `suppliers` (+ per‑item supply terms)
- `purchase_orders`, `transfer_orders`, `transfers`, `purchasing`, `production`,
  `inventory_counts`, `spot_checks`, quantity & cost adjustments
- `inventory_transactions` ledger (12 types) with running cost
- recipes/BOM: `product_ingredients`, `modifier_option_ingredients`,
  `inventory_item_ingredients` (produced items with yield %)
- automatic depletion on order close; waste/return on void

### 5.5 Marketing
- `discounts` (predefined + open, product/order/both qualification)
- `promotions` (basic/advanced, conditions + rewards, schedule, branch/customer targeting)
- `timed_events` (day/time windows)
- `gift_cards` + transactions
- `loyalty` program + point ledger
- customer `house_accounts` + transactions

### 5.6 Reports
Sales, payments, taxes, tips, gift cards, business days, shifts, tills,
drawer operations, voids & returns, inventory levels/control/history,
menu engineering, speed of service, product & item cost, branches trend.

### 5.7 iPad POS app
Native consumer POS replicating the Foodics cashier app, on **this** backend:
PIN login → open till → menu browse → cart with modifiers/combos →
order type (dine‑in with table, takeaway, delivery, drive‑thru) → kitchen send →
discounts/charges → split/join → multi‑tender payment → **receipt printing over
ESC/POS (LAN + Bluetooth) & AirPrint** → cash drawer kick → KDS → end of day (Z‑report).
Offline‑first with a local queue and sync. Melting Moments visual language
(`--color-primary #8a5a64`, Raleway display / Jost body).

---

## 6. Build order and progress

| # | Phase | State |
|---|---|---|
| 1 | **Foundation** — branches + business days, taxes/tax groups, payment methods, charges, reasons, tags, kitchen flows, devices + printers, sections/tables, roles + staff (PIN), tills/shifts/drawer operations, business settings | ✅ done |
| 2 | **Menu** — price tags, groups, allergens, combos, branch-level overrides | ⏳ partial — product `tax_group_id`, `pricing_method`, `costing_method`, `is_non_revenue` landed; the rest outstanding |
| 3 | **POS ops** — POS order engine, pricing/tax, split tender, kitchen tickets + KDS | ✅ done |
| 4 | **Inventory** | ⬜ not started |
| 5 | **Marketing** — discounts, promotions, timed events, gift cards, loyalty, house accounts | ⬜ not started |
| 6 | **Reports** | ⬜ not started (till X/Z report done) |
| 7 | **Admin UI** | ⬜ not started |
| 8 | **iPad POS app** (`mm-pos`) | ⏳ in progress — domain, API client, ESC/POS printing, receipt/kitchen/Z renderers, LAN transport, cash drawer done and tested; SwiftUI screens and offline queue outstanding |

### Delivered so far

**Migrations** `034_pos_foundation` (22 tables), `035_pos_orders`
(6 tables + 28 order columns + 15 order-item columns),
`036_product_pos_fields` (seeds the UAE VAT group and backfills every product).

**Endpoints** ~120 new, under `/branches`, `/taxes`, `/tax-groups`,
`/payment-methods`, `/charges`, `/reasons`, `/tags`, `/kitchen-flows`,
`/devices`, `/printers`, `/roles`, `/staff`, `/tills`, `/business-settings`,
`/pos/orders`, `/pos/kitchen`.

**Key invariants**
- `business_date` derives from each branch's cut-off, so post-midnight trading
  books against the right day.
- Till cash is recomputed from the drawer ledger, never incremented in place.
- `recalculate()` is the only writer of money on an order; order-level discounts
  are apportioned across lines so per-rate VAT stays correct on mixed-rate checks.
- Every POS endpoint is gated on the role authority matrix in §2.

**Tests** 257 Python (24 covering pricing alone) + 11 Swift, all green.

### Note on repository drift

`033_set_admin_initial_passwords` was applied to production but existed on no
branch in this repo — it lived only inside the deployed API image. It has been
restored to version control so a clean checkout reproduces production.
