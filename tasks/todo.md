# Production consumption on the raw-materials report

## Problem
On the raw-materials shift report, "Used in production" (`production_consumption_quantity`)
is ledger-sourced and reads **0 at close**, because production consumption only
hits the ledger when the production report is *approved/posted* (via
`transfer_service.produce()`). Reports are prefilled from the ledger at
`base_posting_sequence`, and nothing reads the sibling production report, so the
raw-material consumption never shows up during the same close. Result: expected
closing is too high and the whole recipe drawdown lands as unexplained variance.

## Decisions (confirmed with user)
1. **Full fix**: make "Used in production" a *function of the produced goods entered*
   (recipe-derived, visible at close, **non-editable**), reconciled with the ledger
   so nothing double-counts once production posts.
2. **New editable column "Extra production use"** — a *standalone additional
   deduction* of raw materials, NOT a comparison against the recipe figure. For
   consumption the recipe doesn't capture, or for producing goods not set up in the
   system. It subtracts in the net independently, alongside recipe "Used in production".
3. New column posts as a **distinct new ledger movement type** (`extra_production_use`).
4. Delivery: API + admin + mm-pos all built together; push only when whole feature
   (incl. new TestFlight build) is ready.
4. Physical closing count remains authoritative for on-hand (confirmed: the count
   posts an absolute true-up); the two production columns only affect the expected
   closing → variance attribution + categorized COGS.

## API (apps/api)

### New ledger transaction type
- `app/models/inventory.py`: add `EXTRA_PRODUCTION_USE = "extra_production_use"` to
  `InventoryTransactionTypeEnum`; add to `TRANSACTION_SIGN` = **-1**.
- `app/services/inventory/inventory_service.py`: add reference prefix (e.g. `"EPU"`)
  to the prefix map (~line 100). Behaves like `INTERNAL_USE` in `post_transaction`
  (plain outward consumption; falls through generic movement path — no special cost
  branch). `type` column is `String(40)` with **no CHECK**, so no constraint migration.

### New report line column + recipe derivation
- `app/models/inventory_v2.py`: add `extra_production_consumption_quantity`
  `Numeric(20,6)` NOT NULL default 0 to `ShiftInventoryReportLine`.
- `app/services/inventory/report_columns.py`: add
  `_EXTRA_PRODUCTION_USE = ColumnSpec("extra_production_consumption_quantity",
  "Extra used in production", ROLE_OUT, SOURCE_ENTERED, TX.EXTRA_PRODUCTION_USE.value)`;
  insert into `raw_materials` and `packaging` column lists right after `_PRODUCTION_USE`.
- `app/services/inventory/report_service.py`:
  - add `extra_production_consumption_quantity` to `_NET_OUT_COLUMNS`.
  - `_apply_source_columns`: set the new column from the ledger like the others
    (`moved(EXTRA_PRODUCTION_USE, outward=True)`); it's in `_NET_OUT_COLUMNS` so it's
    auto-included in the `prefilled` map. Being `SOURCE_ENTERED` it becomes editable
    and posts via the existing generic delta loop in `post_report` — no posting change.
  - **Recipe-derive `production_consumption_quantity`**: new helper
    `_proposed_production_consumption(db, report)` that, for raw_materials/packaging
    reports, sums over sibling `production`/`finished_goods` reports for the same
    branch+business_date that are **not yet posted**, exploding each produced line's
    entered `production_quantity` via `recipe_service.expand_owner(kind="inventory_item",
    owner_id=item, multiplier=qty, catalog=<loaded once>)`. Final column value =
    `ledger CONSUMPTION_FROM_PRODUCTION (≤ base) + proposed(unposted siblings)`.
    Disjoint by posted-status ⇒ no double count; the pre-existing "ledger moved since
    base" refresh/submit guard (`submit_report` re-checks, ~:825) closes the post-then-
    submit gap the same way every other ledger column relies on refresh.
  - Keep `production_consumption_quantity` out of `editable_columns` (stays non-editable).

### Schema + types
- `app/schemas/inventory_v2.py`: add `extra_production_consumption_quantity` to
  `ShiftReportLineResponse`. `columns` computed field already reflects report_columns.
- Regenerate contract per CLAUDE.md rule 8: `python -m scripts.export_openapi` then
  `pnpm --filter @mm/types generate`. Add field to admin `lib/pos-types.ts` report-line
  type if it's explicitly listed (report grid reads columns dynamically via `lineValue`,
  so mostly transparent).

### Migration
- One Alembic revision: `ALTER TABLE shift_inventory_report_lines ADD COLUMN
  extra_production_consumption_quantity NUMERIC(20,6) NOT NULL DEFAULT 0`.
  (No transaction-type CHECK to touch.)

## Admin (apps/admin) — expected: ~none
- `reports/[id]/page.tsx` renders columns dynamically from the server `columns`
  contract and gates editing on `col.editable`, so the new editable column and the
  now-populated read-only column appear automatically. Verify only.

## Register (mm-pos) — small, localized
The FILL grid (`MMPos/Features/Register/InventoryReportView.swift` + `InventoryReportModel.swift`)
is **fully generic over the server `columns` contract** (renders by `role`/`editable`,
posts edited cells by `key`), so both the new editable column and the now-non-zero
read-only column render + post with **no view/model changes**. Only hardcoded surfaces:
- `MMPos/Models/InventoryReports.swift`: add stored `extraProductionConsumptionQuantity`
  field to `ShiftInventoryReportLine` (+ a `case` in `value(forColumnKey:)`) so the
  server-prefilled starting value displays. (`production_consumption_quantity` already
  maps — non-zero renders automatically, no change.)
- `MMPos/Models/ManagerInventory.swift` (~:162): add
  `("Extra production consumption", -extraProductionConsumptionQuantity)` row to
  `movementBreakdown` for the phone manager review screen.
- Update Swift fixtures: `Tests/InventoryReportTests.swift`, `ManagerInventoryTests.swift`,
  `ReportDecodingTests.swift`.
Shared `MMPos/` fill view ⇒ iPad + iPhone both covered by construction.
Note: mm-pos is a separate repo/build (TestFlight); ship API first (backward compatible —
new field optional in Codable), then the register.

## Tests
- Enum has sign + reference prefix (extend existing guards in
  `test_inventory_costing.py` / contract test).
- `report_columns` contract test: new column present, editable, on raw_materials+packaging.
- `_net_quantity` subtracts the new out column.
- Recipe derivation: unit test that an unposted sibling production report makes the
  raw-material report's `production_consumption_quantity` non-zero at prefill, and that
  once the production report posts + raw-material refreshes it's the ledger value (no
  double count).
- Posting: entering the extra column posts an `extra_production_use` transaction with
  the right sign; physical count still trues up to counted value.

## Verification — DONE
- API unit suite: 3045 passed, 8 skipped (DB-gated). Updated the columns contract
  test + added `test_extra_production_use_is_an_editable_deduction_beside_the_recipe_figure`.
- API integration (local Homebrew PG, migrated to 216): the 5 existing posting/
  reconsume tests pass, plus 3 new in `test_inventory_report_production_consumption.py`
  (unposted sibling shows the drawdown; posted sibling is not double-counted; the
  extra column posts an `extra_production_use` movement). test_inventory_triggers +
  test_reports_agree also green.
  - Caught here: `inventory_transactions.type` DOES have a DB CHECK
    (`ck_inventory_transactions_type_allowed`, from migration 186, not mirrored in
    the model) — the migration now widens it to admit `extra_production_use`.
- Types: regenerated openapi.json + generated.ts (only the new field added). Admin
  `tsc --noEmit` clean (admin renders the grid dynamically from the columns contract,
  so no admin code change).
- Register: `swift test` 369 passed (decoding of the new field + manager breakdown).
  The fill grid is generic over the columns contract; the two-target xcodebuild runs
  at push time via publish-build.sh.

## Remaining before/at push
- Push mm-ecommerce → main (deploys API + runs migration 216 on prod DB) and mm-pos
  → main (publish-build.sh builds both TestFlight apps). Both repos push as hussu97.
