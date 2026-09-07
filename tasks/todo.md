# Inventory report submissions — admin review + POS grouping

## Review (done 2026-09-07)
All items below implemented and verified. Backend: ruff clean, OpenAPI exports,
402 unit tests green (incl. 4 new). Admin: type-check + eslint clean. POS: kit
`swift test` green (incl. new grouping test), both iPad + iPhone targets build.
No DB migration needed — category data rides existing `source_summary` JSONB;
response name fields are transient. OpenAPI diff is purely additive.
Not committed yet (commit-author convention needs confirming — see chat).


Decision (confirmed with user): **keep threshold auto-post**. Low-variance
reports still auto-approve + post at submit (auto-posted ⟹ auto-approved, already
true via `post_report` stamping `approved_by`). Only `pending_approval` reports
need the admin approve/reject/edit flow. Ledger already posts on approval for
those — no change to posting timing.

## Backend (mm-ecommerce/apps/api) — branch `wp-inv-reports`
- [ ] `schemas/inventory_v2.py`: add `branch_name`, `submitted_by_name`,
      `approved_by_name` (all `str | None = None`) to `ShiftReportResponse`.
- [ ] `services/inventory/report_service.py`:
  - [ ] `ensure_tasks_for_till`: add `category_name` + `category_order` to each
        line's `source_summary` (batch-load `InventoryCategory`).
  - [ ] extract `_write_line_edits(db, report, data)` from `save_report`; reuse.
  - [ ] add `edit_pending_report(db, *, report, data, user)` — requires
        `pending_approval`, applies edits, recomputes variance, stays pending.
  - [ ] `submit_report`: email notify on every real submission (both paths).
- [ ] `api/v1/inventory_v2.py` (control_router):
  - [ ] `GET /inventory/shift-reports/{id}` (perm `reports.inventory`) + names.
  - [ ] `PUT /inventory/reports/{id}` → `edit_pending_report` (perm `inventory.counts.approve`).
  - [ ] enrich `list_shift_reports` responses with names.
- [ ] `services/email_service.py`: `INVENTORY_REPORT_RECIPIENTS`,
      `_admin_report_url`, `send_inventory_report_submitted`.
- [ ] `templates/emails/inventory_report_submitted.html`.
- [ ] Regenerate `@mm/types` (schema changed).
- [ ] Tests: edit_pending_report, approve posts edited values, name enrichment,
      category in source_summary, email-on-submit.

## Admin (mm-ecommerce/apps/admin)
- [ ] `pos-api.ts`: `shiftReport(id)`, `editReport(id, body)`, `approveReport(id)`,
      `rejectReport(id, reason)`.
- [ ] `inventory/page.tsx`: new **Report submissions** tab (moved out of Shift
      reports); DataTable + Pagination (W8 sizes) + filters (branch/status/type)
      + search + sortable columns; columns: business date, submitted at
      (localized), branch, submitted by, report, status, progress, variance;
      row click → detail.
- [ ] `inventory/reports/[id]/page.tsx`: detail page — header + line values in
      POS-style columns; edit (pending only) → save; approve / reject(reason).

## POS (mm-pos) — branch `wp-inv-report-grouping`
- [ ] `Models/InventoryReports.swift`: add `categoryName`, `categoryOrder` to
      `InventorySourceSummary`.
- [ ] `InventoryReportModel`: `nonisolated static sections(from:)` grouping by
      category (order by categoryOrder, then within by item name); `var sections`.
- [ ] `InventoryReportView.swift`: render sectioned by category with headers.
- [ ] `Tests/InventoryReportGroupingTests.swift`.
- [ ] Build both targets (MMPos + MMPosPhone).
