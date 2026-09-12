# Checkout contacts, countries, gift receiver, pickup channel & slider cleanup

Branch: `feat/checkout-contacts-receiver-pickup-channel`

## 1. Pickup contact (name + phone mandatory)
- [x] API schema: `PickupContactCreate` + `OrderCreate.pickup_contact` + validator (pickup requires it)
- [x] API service `_persist_order`: persist customer_name/phone from pickup_contact
- [x] Web types: `PickupContactCreate` + `OrderCreate.pickup_contact`
- [x] Web checkout: send pickup_contact in buildOrderCreate; require lastName

## 2. More countries
- [x] PhoneInput PRIORITY: add `AU`
- [x] Firebase SMS region policy (IN/PK/AU/GB/US) — applied via Identity Platform REST; allowlist now AE,IN,PK,AU,GB,US

## 3. Gift receiver (delivery only)
- [x] Model `OrderReceiver` + `Order.receiver` relationship
- [x] Migration `240_order_receivers` (verified up/down/up on throwaway PG)
- [x] API schema: `ReceiverCreate` + `OrderCreate.receiver` (delivery-only validator); `ReceiverResponse` on OrderResponse
- [x] API service: create OrderReceiver row; keep coupon/customer_phone on orderer
- [x] `address_format.delivery_contact(order)` + use in slider/lalamove/noon_send builders; dispatch loads receiver
- [x] Web: receiver toggle + fields in delivery branch; buildOrderCreate.receiver; web types
- [x] Admin: receiver row on order detail; `Order.receiver` in admin types.ts

## 4. Store-pickup sales channel (split online → Website Delivery + Store Pickup)
- [x] order_query: `website_pickup` synthetic code + predicate/clause/label
- [x] dashboard.py by_channel CASE + labels
- [x] pos_reports/_base.py channel column + labels
- [x] daily_sales_email.py columns/labels/_column_for
- [x] admin couriers.ts option + CourierMark icon + orders/page.tsx channel label + SalesTab channel dimension

## 5. Kill legacy `slider` courier (code only; keep status-family + webhook source)
- [x] API: remove SLIDER from FulfilmentProviderEnum + catalog + courier_service + slider_service + fulfilment + orders refresh map
- [x] Admin: remove slider from couriers.ts/types.ts/courier-labels(x2)/provider-labels/ZoneMap
- [x] Migration `241_drop_legacy_slider` (data migrate → slider_car; delete couriers row; keep webhook provider) — verified
- [~] Tests: swap `slider`→`slider_car` in routing/zone tests; keep status-family + webhook tests — SUBAGENT running

## Cross-cutting
- [x] i18n keys in seed_i18n.py (EN+AR)
- [x] Regen OpenAPI + @mm/types (clean diff, only additions)
- [x] ruff check + ruff format
- [x] Verify migrations on throwaway Postgres
- [x] Web + admin tsc --noEmit clean
- [~] Full API unit suite green — after subagent; reconcile channel-split test files (daily_sales_email, pos_reports)
- [ ] Commit
