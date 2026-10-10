# September aggregator GMV audit (P&L 161,090 vs accountant 161,142)

Owner ask (2026-10-10): deep-audit Sept aggregator GMV (incl. VAT) less refunds;
find who is wrong and fix every issue found.

Facts (prod, read-only): P&L reproduces 161,090.00 exactly (GMV 161,320 − refunds
230). Noon/Keeta/Careem/Deliveroo reconcile to their own statements to the fils;
Talabat to its payouts per chain/period except 12–14 Sep (15.04 short).

- [x] Reproduce the P&L figure from prod; reconcile each channel to statements/payouts.
- [x] Lock-key collisions: auto-deliver ≡ scheduler-leader (480A) so the stale
      `out_for_delivery` safety net has never run; sales ≡ daily email (4805),
      finance ≡ business-day (4806), catalog ≡ abandoned checkout (480B).
      Unique keys + a test that scans every `*LOCK_KEY` literal.
- [x] Noon/Deliveroo settled cancellations ignored by `_cancellation_net`:
      AGG-20260924-009 (noon paid +28.66) is in no P&L line; AGG-20260930-054
      (Deliveroo charged −17.90) books −0.85. Trust the figure once the order is
      on a statement.
- [x] Talabat statements never arrive by the nightly finance sweep (1-day window;
      Talabat back-dates statements). September's detailed statement and the
      23–30 Sep payouts are missing. Widen Talabat's finance window.
- [x] Keeta bills are renders frozen when requested: newest render per (shop, week)
      now wins (it was the oldest); payouts carry Keeta's Settlement date as due date.
      22–30 Sep (8,488.08) was rendered 1 Oct for a 2 Oct settlement; it stays
      "pending" until a fresh render exists (Keeta → Billing → Download, per shop).
      22–31 Aug (1,259.01) is a one-day partial (22 Aug only): no full bill for
      that cycle was ever rendered.
- [ ] After deploy: confirm auto-deliver books AGG-20260928 (noon FG9SNNLIQ25AXAA),
      migration 313 writes 9 rows, and the nightly finance pass lands Talabat's
      September statement (or run a Talabat finance range for 1–30 Sep by hand).

## Review
- Bridge: P&L 161,090.00 (GMV 161,320 − refunds 230). + 40.00 noon
  FG9ONNBM32FWXVA, a sale on noon's statement, now booked as 28.66 compensation
  (not GMV) = 161,130.00. Accountant 161,142.00: 12.00 unexplained without their
  per-channel sheet. Likely a Talabat in-transit compensation of 12.00 (3863208103
  on 1 Sep or 3914253124 on 25 Sep).
- Accountant's sheet (Online Sept Sale 26): 161,142 = Talabat 75,467 + Noon 30,085
  + Careem 6,935 + Keeta 41,050 + Deliveroo 7,605. Against ours: Talabat +147
  (counts the 5 compensated cancellations: 1,207+5 = 1,212 orders, 168.60; 21.60
  left for Talabat's Sept statement), Noon −150 (noon statement sales: net of the
  190 shop-funded discounts, plus the 40 cancelled-but-billed order), Keeta +55
  (likely 5167841430845412, gross 55, Keeta-compensated), Careem/Deliveroo exact.
- Their commission/received: Careem and Deliveroo commissions look swapped
  (Careem fees 2,181.94 ex VAT; Deliveroo 2,334.60 ex VAT); Deliveroo received
  5,423 vs 5,153.54 on its statements. Keeta 22–30 Sep payout (8,488.08) shows
  `pending` because the bill is never re-read after its first download.
- Talabat 12–14 Sep paid 15.04 less than our order data implies. Wait for the
  September detailed statement to name the order.
- 4,539 unit + P&L integration tests pass; migration 313 up/down/up on Postgres 16.

# Third-party courier cost: entered by hand, never the quote

Owner ask (2026-10-05): MM-20261003-002 went out with a third party and the P&L
booked its 49.00 checkout quote as courier cost. Remove it for third parties;
instead let a person enter what was paid once delivered (VAT inclusive by
default), counted as courier charges in the P&L.

- [x] `OrderDelivery.courier_cost` (hybrid): third party → `cost_total` only; others unchanged (invoice, else quote).
- [x] P&L, economics, delivery response, dashboard (fee total, confirmed/pending, per-courier), daily sales email, custom orders read it; a third party with no entry is not "fees pending".
- [x] Reassigning to a third party clears the previous courier's fare; migration 312 clears the two already left (guarded to exact rows/values).
- [x] `PUT /orders/{n}/delivery/courier-cost` (`orders.manage`, delivered third-party only, `vat_inclusive` grosses up 5%), audited.
- [x] Admin delivery panel: renders the API's `courier_cost`; optional editor on a delivered third-party order.
- [x] Types regenerated; tests (unit + integration on Postgres).

## Review
- Full API suite on a local Postgres: 5,093 passed. Admin type-check, lint (no new warnings), 143 tests pass.
- MM-20261003-002's P&L moves from courier 49.00 / PC3 28.29 to courier 0 / PC3 74.95 until a cost is entered.

# Foodics as the fallback when GrubOps lists an order but won't serve its detail

Owner ask (2026-10-05): a GrubTech-branch order GrubOps lists but 404s on
`getOrderInfo` must still reach the register and still be Packed/Cancelled from
it, exactly as today. Failsafe, all edge cases, no regression.

Facts established on prod data: every GrubOps-listed order is also a Foodics
order (GrubTech publishes it within a second); GrubOps line `externalId` IS the
Foodics product / modifier-option id (1:1, 43 items + 127 options over 3,218
orders, zero conflicts); Σ Foodics product totals − Foodics discount == GrubOps
`totalPrice` (incl. discounted Noon orders); Foodics `total_price` adds the
marketplace delivery charge and its `customer` is a GrubTech placeholder — use
neither. The summary carries customer name/phone, payment method and status.

- [x] Foodics client: `list_recent_orders(branch_id, page)` with products/options/branch includes.
- [x] `grubops/grubops_foodics_fallback.py`: find the one Foodics order matching the summary (mapped Foodics branch, canonical channel, exact external id, ±30 min), refuse on 0 / >1 / money disagreement / no live lines; translate Foodics ids → GrubOps recipe/modifier ids (order history, then unique exact-name on approved map); build a GrubOps-shaped `info` (publish history carries the Foodics id) marked `_fallback`.
- [x] `_ingest_one`: on 404, after a 90 s grace and within 12 h, build the info and run the unchanged `ingest` (create/adopt, register, push, stock, Foodics id). A fallback order whose summary status moves while detail still 404s re-applies status. When the real detail lands, it replaces the raw and fills any customer field the summary lacked.
- [x] Gate on `foodics_orders_service.is_enabled()`; any Foodics error → today's behaviour (wait, retry next tick).
- [x] Tests: matcher, translation, money refusal, loop wiring (grace, gate, status re-apply, detail upgrade), no change for the served-detail path.
- [x] ruff + full API unit suite; dry-run the builder against prod for Barsha 3937792428 (read-only).

## Review
- 4,500 unit tests pass (12 skipped); ruff clean. New: 33 fallback tests + 7 loop/upgrade tests.
- Read-only prod dry run, Barsha 3937792428: matched Foodics #20485, 55.00, box + 3 options all resolve to MM product/options, rider code 2663, note "No cutlery.", Foodics id cached.
- Rebuilt all 55 GrubOps-served orders of 2026-10-05 from Foodics: 55/55 identical match, total and lines (recipe + modifier ids). Only Careem's driver code differs from GrubOps' sequence number, and the stored Careem codes are already last-4, which is what the fallback produces.
- Not covered (separate): a GrubOps *listing* outage (500s, 18:53–19:37 on 2026-10-05) — no summary, so nothing to fall back from.

# Barsha Heights menu PDF → "Melting Moments Cafe" + modifier grouping

Scope confirmed with owner: **menu PDF only** (receipts stay "Attibassi Coffee" — do NOT touch the `najm` legal-entity row / no migration).

## 1. Logo asset (GCS)
- [ ] Resize the cafe logo to 512×512 (match existing `melting-moments.png`), save as `melting-moments-cafe.png`.
- [ ] Upload to `gs://mm-product-images/logos/melting-moments-cafe.png` (public, cache 300s), same as courier-logo pattern.
- [ ] Verify the public URL serves.

## 2. Menu-PDF brand override for Barsha (najm) — no DB change
- [ ] In `menu_pdf/builder.py::_resolve_brand`, add a menu-PDF-only override keyed by legal-entity `reference`: `najm` → brand "Melting Moments Cafe", the new cafe logo URL, `MELTING_MOMENTS_THEME`. Documented as presentation-only (receipts unaffected).

## 3. Modifier grouping — one shared header per unique size signature
Replace the "single dominant tuple per section" layout with per-signature column blocks so S/M/L and Single/Double each print one header once (no repeated modifier headers).
- [ ] `view_models.py`: add `MenuColumnBlock(columns, items)`; `MenuSection` now holds `blocks: list[MenuColumnBlock]` (drop `items`/`columns`).
- [ ] `builder.py`: replace `_apply_column_layout` with `_build_blocks(items)` — cluster by exact variant signature (first-appearance order), fold a strict-subset size set into a lone superset block, single-price items → one plain block.
- [ ] `templates/menu.html.j2`: iterate `section.blocks`; colhead + price cells key off `block.columns`.
- [ ] `render.py::prepare_menu_assets`: iterate `section.blocks[].items` for image prep.
- [ ] `tests/unit/test_menu_pdf.py`: update MenuSection/blocks construction; rewrite column tests for `_build_blocks`; add mixed-signature-two-blocks test; add najm brand-override test.

## 4. Verify
- [ ] `pytest tests/unit/test_menu_pdf.py`.
- [ ] Render smoke (if WeasyPrint present locally) → eyeball Barsha PDF.
- [ ] Commit (author: Hussain Abbasi <h_abbasi97@hotmail.com>, no co-author trailer).

## Review — done
- Logo uploaded: `gs://mm-product-images/logos/melting-moments-cafe.png` (512×512, public, 200 OK).
- Menu-PDF brand override (`_MENU_BRAND_OVERRIDES`, builder.py): `najm` → "Melting Moments Cafe" + cafe logo + Melting Moments theme. **Entity row untouched → receipts stay Attibassi.** No migration.
- Modifier grouping: `_build_blocks` clusters items by exact size signature; strict-subset sizes fold into a lone superset; single-price items → one plain block. View model now `MenuSection.blocks: list[MenuColumnBlock]`. Template + render.py updated.
- Tests: `pytest tests/unit/test_menu_pdf.py` → 29 passed, 1 skipped (WeasyPrint/Pango absent). ruff check + format clean.
- Visual: rendered a Barsha-like sample to HTML and eyeballed it — MM Cafe logo + pink theme; Hot Coffee shows one S/M/L header (Mocha M/L folded), one Single/Double header, Filter Coffee plain. Correct.
- Not committed: working tree carries another session's unrelated changes (operations.py, transfers.py, recost script) and we're on `main`. Left for a deliberate branch/commit.

## Files changed
- apps/api/app/services/catalog/menu_pdf/view_models.py (add MenuColumnBlock; MenuSection.blocks)
- apps/api/app/services/catalog/menu_pdf/builder.py (brand override; _build_blocks)
- apps/api/app/services/catalog/menu_pdf/render.py (iterate blocks)
- apps/api/app/services/catalog/menu_pdf/templates/menu.html.j2 (blocks loop)
- apps/api/tests/unit/test_menu_pdf.py

---

# Customer delivery areas + address geocoding

## Plan
- [ ] Trace the customer cache, delivery-zone geometry, and aggregator address ingestion paths.
- [ ] Add a filter-aware delivery-area cache/API that aggregates valid UAE address coordinates by customers, revenue, or AOV and includes the currently live delivery polygons.
- [ ] Add a Delivery areas customer tab with the existing date/search controls shared by both tabs.
- [ ] Add guarded Google Maps geocoding for address-bearing aggregator orders that lack coordinates; never retry normal channel rows that already have coordinates and accept failed/out-of-UAE results without failing the pull.
- [ ] Add focused API/service/UI tests, regenerate OpenAPI contracts, run linters and relevant tests.
- [ ] Commit the completed feature as Hussain Abbasi.
