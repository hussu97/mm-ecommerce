# Repo maintainability & readability audit

Goal: can an AI agent read this codebase and change it correctly? Full-repo
audit, then remediation in three phases.

## Phase 1 — live defects
- [x] Admin `Order` missing `low_order_fee` — the totals panel did not reconcile by the fee amount
- [x] Admin totals hardcoded "VAT included (5%)" beside an unread `order.vat_rate`
- [x] Admin `OrderStatus` carried 8 of 11 values — refunded orders rendered `undefined` to staff
- [x] Web `AddressCreate` made the map pin optional; neither address form checked for one
- [x] `NOON_SEND_*` fares in 2 of 5 checklist places, so the secret could never reach production
- [x] `certificates_reachable` health probe written and never called
- [x] Six dead backend functions removed; a seventh (`_load_polygon`) found during Phase 3

## Phase 2 — make the canon true
- [x] `AGENTS.md` was a stale fork missing all 10 rules and teaching the four-place checklist
- [x] `ui_translations` exception written into rule 7 (the boot seed overwrites migrations)
- [x] Audit doc P3-14 marked reversed — it still recommended what rule 7 overturned
- [x] `app/core/money.py`: one rounding mode, replacing 8 helpers running two
- [x] Rule 2 — two silent commits explained; rule 4 — `tills.py` onto `require()`; rule 5 — `BackgroundTasks` retired
- [x] Rule 6 — migration 138 constrains the five status columns 099 left behind
- [x] `alembic/env.py` — `include_object` (autogenerate proposed dropping 41 indexes), `compare_type`, no silent `ImportError` fallback
- [x] `[tool.ruff]` added; isort enabled and applied
- [x] `packages/config` wired into both apps; hooks de-duplicated; dead turbo task fixed
- [x] Docs index, system-shape doc, `PRODUCTION.md` TOC, lessons index, 4 docs corrected

## Phase 3 — structure
- [x] `api/v1/analytics.py` 1,359 → 5 modules
- [x] `api/v1/delivery_zones.py` 1,581 → 8 modules
- [x] `schemas/pos.py` 1,017 → 12 modules
- [x] `services/pos_reports_service.py` 1,805 → 5 modules
- [x] `app/services/` 74 flat modules → 8 domain subpackages + 23 cross-cutting
- [x] Admin order detail 1,583 → 759; delivery-zones 1,046 → 214; pos-reports 665 → 96; DeliveryEstimates 657 → 229

## Guards added (each verified to fail before its fix)
| Guard | Rule |
|---|---|
| `test_compose_env_allowlist.py` extended to all five places + workflow parity | 9 |
| `test_agent_docs_do_not_fork.py` | — |
| `test_ui_strings_are_not_migrated.py` | 7 |
| `test_money_has_one_rounding_mode.py` | 10 |
| `test_commits_explain_themselves.py` | 2 |
| `test_email_policy.py` widened to every router | 5 |
| `test_service_layout.py` | 11 |
| `packages/types` guards now read `openapi.json` | 8 |
| `apps/admin/lib/conventions.test.ts` | W8, 9 |
| `apps/web/lib/analytics-docs.test.ts` | W10 |
| `apps/web/lib/fetch-convention.test.ts` | 9 |

## Deliberately not done
- **`@mm/types` adoption.** Generated, CI-gated, imported by nothing; 2,651 hand-written lines shadow it, 104 of 163 names have a counterpart. Its own change.
- **`api-server.ts` completion** and the 10 remaining RSC raw fetches. The backlog is a list in `fetch-convention.test.ts` that may only shrink.
- **Large state-extraction refactors**: `checkout/page.tsx` (1,434), `modifiers/page.tsx` (25 `useState`), `analytics/page.tsx`, `ProductForm.tsx`.
- **`couriers/base.py`** — ~250 duplicated lines across the three courier services.
- **`ResourcePage` `paginated` default.** Checked and rejected: the prop's docstring is right, and `purchase-orders` — counted among the "five unpaginated tables" — does not use `ResourcePage` at all.
- **`B`/`UP` ruff rules** — 1,822 findings, almost all cosmetic. Own pass, own review.
- **A blanket rule-4 guard** — needs a customer-vs-staff distinction the codebase does not encode.

## Review
Verified against a real PostgreSQL 16, so the 40 DB-backed integration tests that
skip without one ran too: **2219 passed, 2 skipped**. `ruff check` and
`ruff format --check` clean over 589 files. Migration 138 validated up, down and
up again, and its CHECK proven to reject an off-script value. Both apps `tsc`
clean; 509 web + 54 admin vitest passing; `export_openapi --check` and
`@mm/types check:fresh` current. Admin lint warnings down from 10 to 3.
