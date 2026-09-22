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
