# Inventory & Recipe value visibility (admin)

## Goal
More integrated cost/value visibility in the admin app. **No change to cost
calculations or the FIFO cost-layer model** — reuse existing server-side cost
services and surface the figures.

## Findings (how cost already works)
- Item cost = weighted avg of surviving FIFO layers per storage unit
  (`cost_layer_service.item_average_cost/costs`). No catalogue cost column, no cache.
- `InventoryLevel.total_value` (server-computed, per item×warehouse) is already
  returned by `GET /inventory/levels` and already carried on the FE `InventoryLevel`
  type — but the items page pivot drops it.
- Recipe cost = each line priced at its ingredient's weighted-avg cost, converted
  to the ingredient unit via `inventory_service.canonical_cost_for_unit` (mirrors
  legacy `_recipe_response`). v2 recipe endpoints currently return no cost.
- Produced items get a FIFO layer at production time (live component cost) — so a
  produced item's inventory value already reflects recipe cost at production. No
  change needed there; the recipe page shows the *current* theoretical unit cost.

## Plan

### Backend (money math stays server-side — rule #10)
- [ ] `InventoryItemResponse`: add `ingredient_unit_cost` (avg cost converted to
      ingredient unit). Populate in `_items_with_cost` via `canonical_cost_for_unit`.
- [ ] `RecipeOwnerRow`: add `unit_cost` + `batch_cost` (nullable). Compute in
      `recipe_catalog_service.list_recipe_owners`: price current-version lines at
      each ingredient's avg cost → basis total; unit = total (unit basis) or
      total/batch_yield (batch basis); batch_cost set only for batch basis.
- [ ] Regenerate `packages/types` (export_openapi + pnpm generate) — rule #8.

### Frontend — Inventory Items page
- [ ] Pivot: also capture per-branch value (sum `level.total_value`/quantity across
      that branch's warehouses; OR the below-min flag).
- [ ] Per-branch cell: show value under the quantity.
- [ ] New per-item "Value" column (sum of branch values).
- [ ] Total inventory value banner at top — updates with search/filters, NOT
      pagination (computed from ResourcePage's pre-pagination `visible` set).
- [ ] `ResourcePage`/`ListPage`: add optional `summary` slot fed the filtered rows.

### Frontend — Recipes
- [ ] `RecipeOwnersPage`: show recipe unit cost, and batch cost when basis=batch.
- [ ] `RecipeEditor`: per-line unit cost (server-quoted `ingredient_unit_cost`) +
      line cost, and a live recipe total (unit, and batch when applicable).
- [ ] `pos-types.ts` `InventoryItem`: add `ingredient_unit_cost`.

### Verify
- [ ] Backend unit tests for the new cost fields; run touched tests + ruff.
- [ ] `openapi.json`/`generated.ts` fresh (drift check green).
- [ ] admin `tsc --noEmit`, lint, vitest for touched files.
- [ ] Commit (author Hussain Abbasi), push to main as requested.

## Review
- **No cost logic changed.** Every figure reuses existing server-side cost:
  `cost_layer_service.item_average_costs` (weighted avg of surviving FIFO layers =
  current stock at PO/production costs) and `inventory_service.canonical_cost_for_unit`
  (storage→ingredient conversion). No cache table, no new rounding, no production path
  touched. Produced items keep costing from their FIFO production layers as before.
- **Inventory Items page**: total-value banner reflects the filtered set (not the
  page — computed from ResourcePage's pre-pagination `visible`); each branch cell
  shows its FIFO value under the qty; a per-item "Value" column sums the branches.
  Values come straight from the server-quoted `InventoryLevel.total_value`; the
  client only sums.
- **Recipes list**: `RecipeOwnerRow` gains server-computed `unit_cost` (+ `batch_cost`
  for batch-basis recipes); rendered as "/ unit" and "/ batch of N".
- **Recipe editor**: per-line unit cost = the ingredient's server-quoted
  `ingredient_unit_cost`; per-line and total figures are a live estimate for the
  (possibly unsaved) edit — the authoritative saved figure is the recipes list.
- **Verification**: full API suite 3875 passed / 5 skipped (real Postgres); new
  recipe cost-rollup tests (unit-basis price, batch split-by-yield, absent/zero);
  admin tsc + lint (0 errors) + 111 vitest; ruff check/format clean;
  openapi.json/generated.ts regenerated and drift-check green.
