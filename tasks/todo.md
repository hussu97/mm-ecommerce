# Aggregator couriers + POS/admin refinements + release

## Backend (mm-ecommerce)
- [x] Migration 133: couriers.logo_url + is_aggregator, orders.aggregator_delivery_fee + aggregator_display_code; seed 9 courier rows w/ logos
- [x] Courier model fields; Order fields
- [x] courier_catalog service (DB-cached logos + channel normalisation) + startup warm
- [x] CourierBadge schema; wired into OrderResponse, OrderListResponse, OrderDeliveryResponse, PosOrderResponse
- [x] Ingest audit fixes: channel from header; driver short code -> aggregator_display_code -> courier_reference (POS prints short); delivery_fee=0 + aggregator_delivery_fee (customer-facing, not in sales); notes already carried
- [x] Admin list channel=aggregator + courier filter
- [x] Courier logos generated (256x256 uniform) + upload script (run on VM at release)
- [x] GrubOps portal endpoints: search + sort for mappings & orders
- [x] Aggregator-safe order-detail actions (backend guards already via lifecycle; confirm)
- [x] Regenerate packages/types
- [x] Full backend pytest green

## Admin (apps/admin)
- [x] Order list: courier logo in channel column; channel (incl aggregator) + courier filter
- [x] Order detail fulfillment: courier logo beside provider; aggregator courier logo
- [x] Order detail actions: hide mark delivered/undelivered/packed/dispatch etc for aggregator (read-only + notes)
- [x] GrubOps portal: search box + filters + alphabetical sort across tabs

## POS (mm-pos)
- [x] Courier name + logo on all order stages (incoming, active, packed, handed over)
- [x] RemoteImage logo from courier.logo_url (DB-driven, not hardcoded)
- [x] Website stock tab: item thumbnail + alphabetical sort
- [x] Settings button visible on all tabs (iPad PadCheckPane)
- [x] Confirm notes print (website + aggregator) and driver short code prints
- [x] swift build + swift test + xcodebuild both schemes

## Release
- [x] QA/audit everything from origin main
- [x] Commit all repos (author Hussain Abbasi)
- [x] Push, deploy, verify prod green
- [x] Run upload_courier_logos on VM; verify logos live
- [x] Verify orders flowing on prod VM


## Review (2026-08-23)
All items complete. Backend 2116 pass + ruff clean + migration 133 up/down validated on PG16. Admin tsc 0 / lint 0 errors. Web tsc 0. Types regenerated in sync. mm-pos: 285 swift tests pass, iPad + iPhone BUILD SUCCEEDED. Logos generated (9 × 256px) + upload script ready for the VM.
