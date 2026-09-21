# Auto-approve inventory reports + variance email summary

## Problem
- Staff-submitted shift inventory reports with a variance go to `PENDING_APPROVAL`
  and only reach the stock ledger once an admin approves. Reports sitting
  un-posted leave the ledger un-reconciled and corrupt later stock movements.
- Owner wants: auto-approve **and** post every report on submit, regardless of
  variance; and the notification email (to fahimakhtarabbasi@gmail.com) should
  list the items where there is a variance.

## Plan
- [x] Trace the submit → approval → post flow (`report_service.submit_report`,
      `post_report`, `_notify_report_submitted`) and the email
      (`email_service.send_inventory_report_submitted`,
      `templates/emails/inventory_report_submitted.html`).
- [x] `submit_report`: always set status APPROVED and call `post_report`; drop the
      variance/opening-count `requires_approval` computation and the now-dead
      threshold/settings lookup. Keep the confirmed-line and competing-movement
      guards (unrelated to approval).
- [x] `_notify_report_submitted`: drop `requires_approval`; build a per-line
      variance summary (physical-count lines with non-zero variance) and pass it
      to the email.
- [x] `email_service.send_inventory_report_submitted`: drop `requires_approval`,
      accept `variance_lines`, update subject to "posted".
- [x] Template: report always auto-posted; add a variance-summary table
      (Item / Expected / Counted / Variance / Value), with a clear "no variances"
      line when empty.
- [x] Update/extend tests (`test_inventory_report_review.py`).
- [x] Run the touched tests, commit, push, open PR.

## Review
- `submit_report` now always sets the report to APPROVED and posts it to the stock
  ledger on submit — no report ever parks in PENDING_APPROVAL, so the ledger is
  reconciled immediately and later stock movements are computed against fresh
  on-hand. Removed the now-dead threshold/settings lookup and `requires_approval`
  computation. Kept the confirmed-line and competing-movement guards untouched.
- Added `_variance_summary(report)`: physical-count lines with a non-zero variance,
  formatted (item, unit, expected, counted, signed variance, value).
- `_notify_report_submitted` / `email_service.send_inventory_report_submitted`:
  dropped `requires_approval`, pass `variance_lines`; subject is now "posted".
- Template `inventory_report_submitted.html`: states the report auto-posted and
  renders a variance-summary table (or a clear "no variances" line). Email still
  goes to fahimakhtarabbasi@gmail.com (`INVENTORY_REPORT_RECIPIENTS`, unchanged).
- No Pydantic schema / OpenAPI change (internal email helper only), so no
  `@mm/types` regeneration needed. No new env var. Admin approve/reject endpoints
  left intact for any legacy pending report.
- Tests: 9 unit tests pass (email render with/without variance, `_variance_summary`
  filtering, auto-post-with-large-variance); related report unit suites green;
  integration posting tests skip locally (no DB) — CI covers them. ruff clean.
