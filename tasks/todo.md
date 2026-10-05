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
