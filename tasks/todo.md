# Aggregator depth E2E (modifiers, sales, statements, payments)

Plan: `.cursor/plans/aggregator_depth_e2e_dadcc6b3.plan.md`
Scope: Deliveroo, Talabat, Noon, Keeta (Careem out this wave).

## Checklist

- [x] P0: Pull main; baseline
- [x] P1: Additive schema + DTOs (`StandardModifier` with qty) + `fetch_statements`/`fetch_payouts`
- [x] P2 Deliveroo: modifiers+qty; statements vs real payouts (no synthetic)
- [x] P2 Talabat: modifiers+qty; Detailed_*.xlsx statement lines over httpx
- [x] P2 Noon: OMS sales+items+modifiers+qty; RMS fee enrich; split finance
- [x] P2 Keeta: structured modifiers+qty; finance push endpoint + warm wiring
- [x] P3: option map resolve/propose; promote OptionSnapshot.quantity; statement_line.mm_order_id
- [x] P4: unit tests green (167 aggregator + 33 bootstrap); OpenAPI regen

## Review

Shipped additive migration `159_agg_depth_enrich`, shared `modifiers.py`, split finance API,
per-channel capture upgrades, promote option mapping with qty, Keeta `/keeta/finance` + warm
push. Careem left as payouts-only. Keeta finance PDF figures still best-effort / truncation_note
until invoice download is richer. Not committed — awaiting user request.

## Statement invoice discovery (2026-08-28)

- [x] Reverted premature CSV/zip R2 archival in Deliveroo/Talabat/Noon/Keeta providers (PDF-first gate)
- [x] Added `apps/api/scripts/audit_statement_invoices.py` (httpx, prod-runnable)
- [x] Added `apps/aggregator-bootstrap/scripts/statement_invoice_audit.py` (browser PDF probe)
- [x] Prod httpx audit run — see findings below
- [ ] Browser audit on prod/local hydrated sessions (Talabat PDF Download, Keeta finance zip, Noon/Deliveroo UI)
- [ ] Refresh Talabat session (`needs_bootstrap` on prod at audit time)
- [ ] Wire PDF-first archival after discovery confirms canonical doc per channel
- [ ] Migration `160_agg_stmt_invoice` ready; apply with `159` on deploy

### Prod httpx audit (`2026-08-28T04:37Z`)

| Channel | Result |
|---------|--------|
| Deliveroo | 82 invoices listed; **all `file_type` probes (csv + pdf variants) → 403 HTML** — download likely needs browser cookie session, not JWT-only |
| Talabat | **skipped** — session `needs_bootstrap` |
| Noon | prod code lacks `fetch_statements` (pre-depth deploy); browser audit needed for PDF vs CSV |

**Implication:** VAT invoice archival must be browser-assisted for at least Deliveroo + Talabat; httpx alone insufficient today.
