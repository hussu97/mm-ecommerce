# Aggregator depth E2E — findings (Aug 2026)

Scope: Deliveroo, Talabat, Noon, Keeta (Careem out of scope).  
Plan: `.cursor/plans/aggregator_depth_e2e_dadcc6b3.plan.md`

---

## What shipped in this commit

### Schema (migrations 159 + 160)

| Migration | Adds |
|-----------|------|
| `159_agg_depth_enrich` | Order customer/timeline columns; `aggregator_order_item.modifiers` JSONB; statement `external_outlet_id`; statement line `mm_order_id` + `grain` |
| `160_agg_stmt_invoice` | Statement invoice archive columns on `aggregator_statement` (`invoice_object_key`, `invoice_content_type`, `invoice_original_filename`, `invoice_fetched_at`, `invoice_attachments`) |

### Shared API layer

- `modifiers.py` — `StandardModifier` with quantity; `expand_modifiers()` for round-trip JSON
- `normalized.py` — `StatementsResult` / `PayoutsResult`; extended `StandardOrder` / `StandardStatement`
- `statement_docs.py` — private GCS archive under `invoices/{channel}/{statement_id}/` in `GCS_INVOICE_BUCKET` (auth via ADC / the VM service account)
- `aggregator_base.py` — split `fetch_statements` / `fetch_payouts`; `fetch_finance` wrapper
- `ingest.py` / `promote.py` — upsert new fields; option mapping with qty; statement line `mm_order_id` backfill

### Per-channel capture

| Channel | Sales | Finance |
|---------|-------|---------|
| **Deliveroo** | Modifiers + qty from order detail; customer/timeline | Statement CSV lines from Partner Hub invoices; no synthetic payouts |
| **Talabat** | CSV modifier parsing; balanced item split | GraphQL metadata + Detailed `*.xlsx` bundle → statement lines |
| **Noon** | OMS order history + RMS fee merge | Wallet Statement vs Payment tabs split |
| **Keeta** | Structured modifiers + qty | `POST /keeta/finance` + bootstrap warm push |

### Bootstrap

- `warm.py` / `keeta_pull.py` / `push.py` — Keeta finance payload push
- Audit scripts: `apps/api/scripts/audit_statement_invoices.py` (httpx), `apps/aggregator-bootstrap/scripts/statement_invoice_audit.py` (browser)

### Invoice archival status

**Deferred until PDF discovery completes.** Premature CSV/zip R2 uploads were removed from all providers. Schema + `statement_docs.py` are ready for PDF-first wiring after browser audit confirms canonical VAT documents per channel.

---

## Prod live verification (2026-08-28)

**Prod at verify time:** Alembic `158_merge_grubops_item_map` — migrations 159/160 not yet applied.

| Channel | Sales | Items / modifiers | Finance | Session |
|---------|-------|-------------------|---------|---------|
| **Deliveroo** | ✅ sweep OK | ✅ 24 items; modifiers NULL (159 undeployed) | ✅ reconciliation; net on recon not order row | ✅ live (earlier 401s before refresh) |
| **Talabat** | ❌ TOKEN_EXPIRED | ✅ 34 orders in DB from prior run | ⚠️ 9 statement headers, financially empty; 0 statement lines | ❌ needs bootstrap |
| **Noon** | ✅ 5412 statement orders | ❌ 0 items (OMS path undeployed) | ✅ 49 statements; 0 statement lines | ✅ live |
| **Keeta** | ✅ 1543 orders (warm bulk) | ✅ 1692 items; modifiers NULL | n/a (no statement flow) | ✅ warm one-shot |

---

## Statement invoice discovery (2026-08-28)

Prod httpx audit (`python -m scripts.audit_statement_invoices`):

| Channel | Finding |
|---------|---------|
| **Deliveroo** | 82 invoices listed; all `file_type` probes (`statement_csv`, `tax_invoice_pdf`, etc.) returned **403 HTML** — download likely requires browser cookie session, not JWT-only httpx |
| **Talabat** | Skipped — session `needs_bootstrap` |
| **Noon** | Pre-depth prod code lacked `fetch_statements`; browser audit needed for PDF vs CSV on wallet UI |

**Conclusion:** VAT invoice archival will be **browser-assisted** for at least Deliveroo and Talabat. Httpx alone is insufficient today.

### Expected canonical documents (from automation reference)

| Channel | Likely VAT doc | Notes |
|---------|----------------|-------|
| Talabat | **PDF** per row Download on Additional statements | Bulk zip is xlsx for line detail, not tax invoice |
| Keeta | **PDF inside zip** from finance bill `downloadUrl` | pdfplumber used in automation |
| Deliveroo | Unknown PDF variant — Partner Hub `/reports/invoices` | Automation only documents `statement_csv` |
| Noon | Wallet export is tabular CSV | PDF tax invoice TBD via browser |

---

## `uv.lock` decision (aggregator-bootstrap)

**Do not commit.** Prod Docker (`apps/aggregator-bootstrap/Dockerfile`) runs `pip install .` from `pyproject.toml` only. CI (`pr-check.yml`) uses `uv pip install -e` without a lockfile. The file was a local `uv sync` artifact and is gitignored at `apps/aggregator-bootstrap/uv.lock`.

Contrast: `apps/api/uv.lock` remains tracked for local API dev reproducibility but is also not used in the API Dockerfile.

---

## Post-deploy checklist

1. Apply migrations 159 + 160 on prod (`alembic upgrade head`)
2. Refresh **Talabat** session (headed login / `aggregator-bootstrap login --channel talabat`)
3. Run safe 1-day sweeps only: `sweep_channel_once(channel, "sales"|"finance", lookback_days=1)` — no promote smoke, no 365-day backfill
4. Run browser invoice audit with hydrated sessions
5. Wire PDF-first archival after discovery
6. Optional: admin presigned URL for archived invoices

---

## Test coverage

- 184+ aggregator unit tests (Deliveroo, Talabat, Noon OMS, Keeta finance/modifiers, ingestion, promotion)
- Bootstrap tests for Keeta push contract
- OpenAPI / `@mm/types` regenerated for Keeta finance endpoint
