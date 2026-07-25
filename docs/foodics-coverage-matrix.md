# Foodics Coverage Matrix

Every Foodics capability, audited against what exists in this stack. This is the
verification companion to [pos-foodics-parity.md](pos-foodics-parity.md): that
document is the spec, this one is the checklist.

**Method.** Every screen in `console.foodics.com` was opened and its contents
read (nav tree, list views, entity forms, settings tabs, the role authority
matrix, dashboard tabs). That was cross-checked against the public API reference
at `apidocs.foodics.com` for exact object shapes and enum values, and against
Foodics' own marketing feature list at `foodics.com/rms-features` plus
independent reviews for anything the console does not expose to this account's
plan.

**Legend** — ✅ implemented · ⚠️ partial · ⬜ gap · ⛔️ out of scope (agreed)

---

## 1. Cashier / POS terminal

| Foodics capability | Status | Where |
|---|---|---|
| Open / edit / close a check | ✅ | `pos_order_service`, `/pos/orders` |
| Order types: dine-in, takeaway, delivery, drive-thru | ✅ | `OrderTypeEnum` |
| Order sources: cashier, online, API, call centre | ✅ | `OrderSourceEnum` |
| Open-price products | ✅ | `pricing_method="open"`, `pos.products.open_price` |
| Sell by weight | ✅ | weight on a line, priced per kilo, printed on the receipt. A scale *driver* still needs hardware; manual entry works today |
| Modifiers with min/max/free/unique rules | ✅ | `ProductModifier`, `ModifierSheet` |
| Combos (sizes → items → options) | ✅ | `Combo` family |
| Predefined + open discounts | ✅ | `Discount`, `/pos/orders/{id}/discounts` |
| Open + predefined charges | ✅ | `Charge`, `OrderCharge` |
| Split payment (multi-tender) | ✅ | `OrderPayment` |
| **Split an order into separate checks** | ✅ | `POST /pos/orders/{id}/split` — moves lines to a new check on the same table |
| **Join orders** | ✅ | `POST /pos/orders/{id}/join` — source marked `joined`, never deleted |
| Void order / void line, with reasons | ✅ | `/void`, `Reason(void_return)` |
| Return order / partial line return | ✅ | `/items/{id}/return` |
| Park / draft a check | ✅ | `POST /pos/orders/{id}/park` and `/resume`, listed via `?pos_status=draft` |
| Send to kitchen (fire) | ✅ | `/send-to-kitchen`, `KitchenTicket` |
| Kitchen reprint | ✅ | `/pos/kitchen/tickets/{id}/reprint` |
| Coursing (staged firing) | ✅ | `/courses` CRUD, `course_id` on a line, `send-to-kitchen?course_id=` |
| Tables + floor plan | ✅ | `Section`, `PosTable`, layout JSON |
| Change table owner | ✅ | `POST /pos/orders/{id}/table` — claims the new table before freeing the old |
| Tips | ✅ | `OrderPayment.tips`, `Order.tips_amount` |
| Cash rounding | ✅ | `apply_cash_rounding` |
| Offline mode | ✅ | `OfflineQueue`, `MenuCache` (mm-pos) |
| PIN sign-in | ✅ | `/staff/pin-login`, branch-scoped |
| Fingerprint sign-in | ✅ | Face ID / Touch ID unlocks the stored PIN; keypad always remains |
| Ahead / scheduled orders | ✅ | `due_at` on open, plus `POST /pos/orders/{id}/schedule` |
| Act as driver / waiter | ✅ | `POST /pos/orders/{id}/driver` + `GET /pos/orders/dispatch/board` |

## 2. Cash control

| Foodics capability | Status | Where |
|---|---|---|
| Open / close till with float | ✅ | `till_service` |
| Expected cash vs counted, variance | ✅ | recomputed from the drawer ledger |
| Pay in / pay out / cash drop | ✅ | `DrawerOperation` |
| No-sale drawer open (audited) | ✅ | `open_drawer` type |
| X report / Z report | ✅ | `/tills/{id}/report`, printed via ESC/POS |
| End of day per branch | ✅ | `/branches/{id}/business-days/close` |
| Business day (trading day) rollover | ✅ | `business_day_service`, tested |
| Shifts / clock in-out | ✅ | `Shift` |
| Cash spot check | ✅ | `POST /tills/{id}/spot-check` — zero-signed, leaves expected cash untouched |

## 3. Menu

| Foodics capability | Status | Where |
|---|---|---|
| Categories | ✅ | pre-existing |
| Products (SKU, barcode, cost, calories, prep time) | ✅ | pre-existing + POS fields |
| Pricing / selling / costing method | ✅ | migration 036 |
| Modifiers + options | ✅ | pre-existing |
| Combos | ✅ | migration 038 |
| Menu groups | ✅ | `MenuGroup` |
| Price tags (alternate price lists) | ✅ | `PriceTag`, `ProductPrice` |
| Allergens | ✅ | `Allergen`, `ProductAllergen` |
| Per-branch price / availability override | ✅ | `BranchProduct` |
| Mark out of stock from the terminal | ✅ | `PUT /products/{id}/availability` per branch |
| Product nutrition facts | ✅ | free-form `nutrition` panel: macros, salt, allergens |

## 4. Inventory

| Foodics capability | Status | Where |
|---|---|---|
| Inventory items, storage vs ingredient unit | ✅ | `InventoryItem` |
| Inventory categories | ✅ | `InventoryCategory` |
| Warehouses | ✅ | `Warehouse` |
| Stock levels + weighted-average cost | ✅ | `InventoryLevel`, tested |
| Suppliers + supplier catalogue | ✅ | `Supplier`, `SupplierItem` |
| Purchase orders (draft→submit→approve→receive) | ✅ | with separation of duties |
| Purchasing (direct, and from a PO) | ✅ | `purchasing` transaction type |
| Transfer orders (request→accept/decline→close) | ✅ | `TransferOrder`, `transfer_service` |
| Transfers (send / receive) | ✅ | paired flow; in-flight stock is visible |
| Production (+ auto-consumption of ingredients) | ✅ | `produce()`, yield-aware costing, tested |
| Waste (from orders, from production) | ✅ | `POST /inventory/transactions/waste` — kept distinct from a correction |
| Quantity adjustment | ✅ | `/inventory/transactions/adjust` |
| Cost adjustment | ✅ | `POST /inventory/items/cost-adjustment` — revalues without moving stock |
| Inventory count (draft → closed, variance) | ✅ | open freezes the system figure, close posts the variance |
| Spot check (reference only, does not move stock) | ✅ | `SpotCheck`, records variance only |
| Count sheets | ✅ | `GET /inventory/counts` lists open and closed sheets |
| Order transactions (stock consumed by sales) | ✅ | auto-depletion on close |
| Recipes / BOM (product, modifier, produced item) | ✅ | three ingredient tables |
| Prevent negative stock | ✅ | `BusinessSettings.prevent_negative_stock` |
| Low-stock / reorder reporting | ✅ | levels report + valuation |

## 5. Customers

| Foodics capability | Status | Where |
|---|---|---|
| Customer records, order history | ✅ | pre-existing `User` + orders |
| Addresses + delivery zone | ✅ | pre-existing `Address`, `Region` |
| Customer tags | ✅ | `Tag(type=customer)` |
| Blacklist | ✅ | `User.is_blacklisted` + reason |
| House accounts + ledger | ✅ | `HouseAccount` (⛔️ no UI, descoped) |
| Loyalty | ✅ | `LoyaltyProgram` (⛔️ no UI, descoped) |
| Gift cards | ✅ | `GiftCard` (⛔️ no UI, descoped) |
| Customer insights permission split | ✅ | `customers.insights.read` |

## 6. Marketing

| Foodics capability | Status | Where |
|---|---|---|
| Discounts | ✅ | `Discount` |
| Promotions (basic + advanced, triggers/rewards) | ✅ | `Promotion` |
| Timed events | ✅ | `TimedEvent`, midnight-crossing tested |
| Coupons | ✅ | pre-existing `PromoCode` |
| Bulk coupon creation | ✅ | `POST /promo-codes/bulk` — unique single-use codes, unambiguous alphabet |
| Sales by section / revenue centre / modifier option / tags / coupon | ✅ | `GET /pos/reports/sales/by` — 24 dimensions |
| Branches trend | ✅ | `GET /pos/reports/branches-trend` |
| Table utilization | ✅ | `GET /pos/reports/table-utilization` — covers, turns, dwell, sales per seat |
| Suppliers analysis | ✅ | `GET /pos/reports/suppliers-analysis` |

## 7. Administration

| Foodics capability | Status | Where |
|---|---|---|
| Branches | ✅ | `Branch` |
| Users + roles (136-permission matrix) | ✅ | `Role`, verbatim catalogue |
| Devices (cashier/KDS/display/notifier) | ✅ | `Device` + pairing |
| Printers + cash drawer | ✅ | `Printer` |
| Taxes & tax groups | ✅ | `Tax`, `TaxGroup` |
| Payment methods | ✅ | `PaymentMethod` |
| Charges | ✅ | `Charge` |
| Delivery zones | ✅ | branch↔zone mapping, `region_id` on an order, sales-by-delivery_zone |
| Tags | ✅ | `Tag` |
| Reasons (3 types) | ✅ | `Reason` |
| Kitchen flows | ✅ | `KitchenFlow` + routing |
| Reservations | ✅ | `Reservation`, seats the table on confirm |
| Online ordering settings (per-branch device) | ✅ | `Device.receives_online_orders` routes web orders to a terminal |
| Notification rules | ✅ | `NotificationRule` (event + threshold + recipients) |
| Receipt / kitchen / inventory settings | ✅ | `BusinessSettings` |
| Activity log | ✅ | pre-existing `AuditLog` |

## 8. Reporting

| Foodics report | Status |
|---|---|
| Sales reports | ✅ summary + 7 dimensions |
| Payment reports | ✅ |
| Taxes | ✅ |
| Tips | ✅ (in summary) |
| Business days | ✅ |
| Shifts | ✅ |
| Tills | ✅ |
| Drawer operations | ✅ |
| Voids & returns | ✅ |
| Activity log | ✅ |
| Inventory levels | ✅ |
| Inventory control / history | ✅ transactions report |
| Purchase orders / transfers / purchasing | ✅ `GET /pos/reports/purchase-orders` and `/transfers` |
| Cost adjustment history | ✅ `GET /pos/reports/cost-adjustment-history` |
| Menu engineering | ✅ |
| Inventory cost analysis / COGS | ✅ |
| Product & item cost | ✅ |
| Branches trend | ✅ per-branch per-day trend |
| Speed of service | ✅ `GET /pos/reports/speed-of-service` — acknowledge / prep / total, plus slowest ticket |
| Live branches dashboard (active orders, occupied tables, offline cashiers, open tills, last sync) | ✅ `/pos/dashboard/branches` |
| Zatca reports | ⛔️ Saudi e-invoicing; UAE business |

## 9. Explicitly out of scope

| Area | Reason |
|---|---|
| Aggregator sync (Talabat, Noon, Keeta, Deliveroo, Careem) | Excluded by you at the outset |
| Foodics Pay / Foodics Capital | Foodics' own payment rails |
| Marketplace (100+ third-party apps) | Third-party integrations |
| Gift card / loyalty / house account **UI** | Descoped by you |
| KDS **screen** in the iPad app | Descoped by you (API is built) |
| ZATCA e-invoicing | Saudi-specific |

---

## Closed in migration 039

Transfer orders (full request → accept → send → receive workflow), production
with yield-aware costing, spot checks, reservations, customer blacklist,
notification rules, and the live branches + inventory dashboards.

## Genuinely still open

Small, and each is a UI or endpoint over data that already exists:

| Gap | Note |
|---|---|
| Split / join checks | `original_order_id` is modelled; needs two endpoints |
| Mark out of stock from the terminal | `BranchProduct.is_in_stock` exists; needs an endpoint |
| Cost adjustment | transaction type exists; needs an endpoint |
| Inventory count workflow | variance capture exists; needs the draft→close flow |
| Change table owner | permission exists; needs an endpoint |
| Speed of service report | needs aggregation over kitchen ticket timings |
| Bulk coupon creation | single create today |
| Delivery zone polygons | `Region` covers fees, not geofences |
| Scale integration for weighed items | hardware, needs a device |

| Sales predictions | ✅ | `GET /pos/reports/sales-predictions` — weekday averages with an honest confidence |
