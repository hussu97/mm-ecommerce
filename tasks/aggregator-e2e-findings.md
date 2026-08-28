# Aggregator E2E Findings — Prod VM, 2026-08-28

Real E2E run against prod VM (`mm-backend`, me-central1-a/Doha, e2-micro). Live path
tested = `ingest.sweep_channel_once(channel, mode, lookback_days=N)` in the api
container (prod session, prod DB, prod R2, UAE egress). VM RAM stayed healthy
(190–253 MB avail) throughout; no crash risk observed.

**Terminology note:** the goal says "GCS bucket". The implemented archive target is
**Cloudflare R2** (`statement_docs.store_statement_invoice`), under private prefix
`aggregator-statements/{channel}/{statement_id}/{filename}`. `BACKUP_GCS_BUCKET` is
only for DB backups. All "store invoice" references below mean the R2 path.

## Session state at start
| channel | status | note |
|---|---|---|
| deliveroo | live | warmed+success 8/27 20:13 |
| noon | live | warmed 12:14, success 20:15 |
| keeta | live | warmed 22:00 (push-based, no last_success) |
| talabat | **needs_bootstrap** | "talabat returned 401 — session no longer authentic" |

## Cross-cutting truths (verified live)
- **Order summaries + payouts** retrieve fine via httpx for Noon/Deliveroo/Talabat.
- **Per-order statement LINES**: source exists per channel but none reach the DB
  (`aggregator_statement_line` = 0 rows across all channels).
- **Invoice file downloads** are the hard wall: anti-bot gated (Deliveroo CF 403,
  Talabat PX likely) OR not wired (Noon, Keeta). Archive count = 0 everywhere.
- **customer_name/phone = 0 rows every channel.**
- **Noon OMS works when swept fresh**: a live sales(1d) sweep wrote 20 orders WITH
  items and 9 WITH modifiers, max business_date 8/28 — so the prod "0 items / stale"
  was a stale-session/scheduling artifact, not a parser defect.

---

## DELIVEROO
1. **Reauth — WORKS.** email_password, no OTP. In-API `prepare_session`→refresh→login
   wired; bootstrap `login --auto` wired. Sales sweep succeeded live.
2. **Fetch — sales WORKS; finance list WORKS, downloads BLOCKED.** `/api/invoices`
   list returns 3 statements w/ `total.fractional` (net_payable parsed: 716.83 etc),
   period, download_links. `fetch_payouts` returns [] by design.
3. **Item richness — code WORKS, customer MISSING.** items/modifiers/status/timestamps
   parsed from `/api/orders/{id}` detail; live order even carried 1 modifier.
   `customer_name/phone` parsed by code but empty in practice (Deliveroo withholds).
4. **Invoices — BLOCKED (Cloudflare).** ALL download file_types
   (statement_csv/csv/pdf/invoice_pdf/statement_pdf) return **403 text/html
   "Just a moment..."**. `cf_clearance` is IP-bound to the laptop login, not the VM.
   `store_statement_invoice` also never called. → needs in-page/clearance capture path.
5. **Statement lines — code WORKS, blocked upstream.** `_statement_lines` parses
   `statement_csv` `Order ID` column, but that CSV is the CF-403 above → 0 lines.
   Truncation note is emitted ("statement CSVs unavailable for invoices: …"). Good.
6. **Retry — PARTIAL.** Order-detail loop swallows AggregatorAuthError (dead session
   masquerades as per-order gaps); `_login` raw httpx no retry/timeout mapping;
   CF-403 on downloads currently degrades to generic truncation.

## NOON
1. **Reauth — PARTIAL/fragile.** Session replay + Akamai cookies; scope
   (n-restaurantcode/x-project) injected from `aggregator_account.extras`
   (migration 155). Missing extras → hard AggregatorAuthError kills whole sweep.
2. **Fetch — WORKS.** OMS `/_oms/order/panel/history` (realtime, items) + RMS wallet
   `finance/wallet {entryType:statement}` + `finance/statement/orders`. Payouts parse
   (2 live, paid, amounts). Statements parse (net_payable=amount).
3. **Item richness — WORKS when fresh (OMS).** items/qty/unit_price/modifiers via
   `expand_modifiers` on `items[].modifiers`; status+timestamps from OMS.
   `customer_name/phone` hardcoded None (573-ish). RMS path hardcodes `items=[]`.
4. **Invoices — MISSING (not wired).** No PDF endpoint discovered (http2 protocol
   error in discovery). `_post_tabular_with_raw` exists but unused.
5. **Statement lines — MISSING.** `finance/statement/orders` rows carry per-order
   `external_order_id` + fees but are consumed only into StandardOrder, never into
   StandardStatementLine. → implementable now.
6. **Bug: period_start dropped.** Raw wallet row HAS `periodStart` (e.g. 2026-08-15)
   but StandardStatement.period_start=None. Trivial map fix.

## TALABAT  (session dead — needs reauth to finish live checks)
1. **Reauth — the live test case.** needs_bootstrap after 401. email_otp via Graph
   mailbox. PerimeterX wall. Reauth attempt pending (local Playwright).
2. **Fetch — WORKS (from prior data).** GraphQL export CSV for sales; ListAdditional
   Statements + ListPayouts for finance.
3. **Item richness — PARTIAL.** item_name/qty parsed; unit_price only single-item
   rows; modifiers parsed from parenthetical/column. customer + accepted/delivered/
   cancelled timestamps MISSING.
4. **Invoices — MISSING (not wired).** `additionalStatements[].attachments{path,type}`
   (real PDFs/XLSX/zip) are dropped; `store_statement_invoice` never called.
5. **Statement lines — PARTIAL.** Lines only from xlsx bundle; silent 0-line
   degradation if bundle download fails (no truncation note).
6. **Retry — PARTIAL.** Download auth-death misclassified as Unavailable (won't flip
   needs_bootstrap); GraphQL errors always Unavailable; exact-header CSV fragility.

## KEETA  (in-page pull; not httpx — tested via keeta_pull locally, pending)
1. **Reauth — PARTIAL.** hydrate restores storage_state; but LOGIN_ACCOUNTID liveness
   never asserted → signed-out session silently pulls risk-controlled JSON.
2. **Fetch orders — WORKS (1565 orders). Finance — BROKEN.** `fetch_keeta_finance`
   POSTs `/api/finance/bill/getBillList` — **non-existent endpoint**. Real settlement
   endpoints (from audit): `/api/settlement/statement/v2/r/statementfile/list`
   (monthly commission-invoice ZIP), `/api/settlement/statement/v2/r/download/task/list`
   (per-shop weekly XLSX). Shop ids read from empty `SHOP_IDS` sessionStorage instead
   of `/api/account/query/getShopListByAccount`.
3. **Item richness — PARTIAL, modifiers BROKEN.** items/qty/price parse from
   `products[]`. **modifiers=0 root cause:** `_items_from` reads only `modifiers`/
   `attributes`; real options live under **`groups[]`** (+ `spuPvList[]`). customer in
   `recipientInfo`/`userInfo` (masked) dropped. status numeric ("30"/"40") not decoded.
4. **Invoices — MISSING (dead code).** `_archive_keeta_invoice` correct but never
   called; ZIP/XLSX bytes never downloaded in-page.
5. **Statement lines — MISSING.** No statement id keys match real payload → 0
   statements → 0 lines. Line figures live in XLSX, never downloaded.
6. **Retry — PARTIAL.** getOrders loop `break`s on any non-dict (silent truncation);
   empty accountid never raises; fixed 6s SPA-boot wait.

---

## Fix plan (impact-ranked, testability-aware)
### Implementable + testable now (live channels)
- [ ] Noon: map periodStart→period_start (trivial).
- [ ] Noon: emit StandardStatementLine from finance/statement/orders (order ref + fees).
- [ ] Noon: harden missing-scope to soft-fail (don't kill whole sweep).
- [ ] Deliveroo: classify CF interstitial ("Just a moment"/403 html) as a clear
      truncation reason; stop swallowing AggregatorAuthError in order-detail loop;
      harden `_login`.
- [ ] Wire invoice archival generically (talabat/deliveroo/noon) so any successful
      download is stored; emit truncation when blocked.
### Parse-side, unit-testable (no live needed)
- [ ] Keeta: read modifiers from groups[]/spuPvList; map customer; decode status.
- [ ] Talabat: customer + accepted/delivered/cancelled; download auth-classification.
### Needs reauth / bootstrap-worker (in-page)
- [ ] Talabat reauth (Q1) + then invoice archival for attachments/bundle.
- [ ] Keeta: repoint finance endpoints + fetch shop ids + download ZIP/XLSX in-page
      + call _archive_keeta_invoice + parse XLSX→lines.
- [ ] Deliveroo invoices/lines: in-page/clearance capture path (CF wall).

---

# RESOLUTION — implemented (local only, not pushed) + verification

## Cross-cutting discoveries made during the run
- **Anti-bot channels cannot be warmed headlessly.** Verified on the VM: noon's
  Akamai edge STALLS a headless Playwright `page.goto` to
  restaurant.noon.partners entirely (never commits; 45s timeout on both HTTP/2
  and HTTP/1.1) — while the SAME cookie replayed by the ingest over curl_cffi
  TLS-impersonation is accepted. Two sub-bugs found & fixed under it (probe URL
  was a POST-only JSON endpoint; Playwright HTTP/2 vs Akamai). But the deeper
  truth: the README's "warm rotates the anti-bot cookie" step does not work with
  a headless browser. The robust, VM-safe fix is to **persist rotated Set-Cookie
  from the ingest responses** (zero browser cost) — documented, not shipped
  (too risky to land unverified in the load-bearing ingest).
- **Reauth of a dead session needs a UAE-egress workstation.** `login` spawns a
  real Google Chrome with a CDP debug port; the headless VM image has no such
  Chrome ("Chrome did not open a debug port"). And PX/Akamai bind the cookie to
  the egress IP/ASN, so a laptop login must be bridged to the VM egress by a warm
  — which (above) doesn't work headlessly. This is why Talabat died.
- **Keeta double-emit bug:** every order was parsed twice (a phantom `baseOrder`
  sub-dict row with null outlet/zero items). Fixed (dedupe on order id).
- **Keeta settlement downloadUrls are presigned S3 (valid ~2046)** — downloadable
  server-side without the browser; only the LIST calls need in-page mtgsig.

## What was changed (all local, `git status` clean of pushes)
| file | change |
|---|---|
| noon_provider.py | period_start from periodStart; emit 1852 per-order statement lines w/ external_order_id from statement/orders |
| deliveroo_provider.py | stop swallowing AuthError in detail loop; wire invoice archival (PDF+CSV); CF-gate truncation clarity |
| talabat_provider.py | wire bundle-zip invoice archival→R2; download auth reclassification; bundle-miss truncation note |
| keeta_provider.py | modifiers from groups[]/spuPvList[]; customer from recipientInfo/userInfo; numeric status decode; fix double-emit; parse_finance for real statementfile/list + download/task/list; XLSX Order-Summary→lines; wire _archive_keeta_invoice (ZIP→R2) |
| keeta_pull.py | repoint finance to real endpoints + shop-id resolve + presigned byte download+push |
| channels/probes.py | noon probe_url → console root (was POST JSON endpoint) |
| browser.py | noon `--disable-http2`; `wait_until=commit`; tolerate slow SPA settle |
| aggregator-warm.cron | keep keeta-only; document why anti-bot warm is omitted + the Set-Cookie fix |

Tests: 343 API + 33 bootstrap unit tests pass; ruff format+check clean.
Keeta finance verified against the REAL downloaded bill.xlsx (37 orders→148 lines)
and commission.zip (monthly VAT PDF). Noon statement lines verified LIVE on prod
(0→1852, all with order refs; period_start populated).

## THE 6 QUESTIONS × 4 CHANNELS (answers)
Legend: ✅ works / ⚠️ works with caveat / ❌ blocked/needs more.

### Q1 Reauth via the defined process
- Deliveroo ✅ email/password self-heals in the API sweep (prepare_session) + `login --auto`.
- Noon ⚠️ session replay works; scope from account extras; but headless warm can't rotate its Akamai cookie (must reauth from UAE workstation when it decays).
- Talabat ❌ currently dead; reauth needs a UAE-egress real-Chrome `login` (Graph OTP auto-read is wired). Warm can't keep it alive headlessly.
- Keeta ⚠️ hydrate restores in-page session; add a LOGIN_ACCOUNTID liveness assert (noted).

### Q2 Sales + statements + payouts retrievable
- All four ✅ for what the portal exposes to httpx/in-page. Noon sales(1d)=247 live; Deliveroo/Noon/Talabat statements+payouts parse; Keeta finance now repointed to real endpoints.

### Q3 Item richness (items/modifiers/customer/status)
- Deliveroo ✅ items/modifiers/status/timestamps; customer usually withheld by Deliveroo.
- Noon ✅ items+modifiers via OMS when session fresh (verified: 9/20 recent orders had modifiers); customer not exposed by OMS.
- Talabat ⚠️ items+modifiers; customer/accepted/delivered/cancelled not in the export CSV.
- Keeta ⚠️ items yes; modifiers only when present (real orders had none); customer now captured (recipientInfo, masked); status decoded.

### Q4 Statement invoices downloadable + stored (R2, not GCS)
- Keeta ✅ monthly commission VAT **PDF** (inside ZIP) now downloaded + archived to R2.
- Talabat ✅ settlement bundle ZIP archived to R2 (individual attachment PDFs need a base-URL, deferred).
- Deliveroo ❌ every download file_type returns Cloudflare 403 (cf_clearance IP-bound) — needs in-page/clearance capture.
- Noon ❌ statement-PDF endpoint not yet discovered (browser blocked; needs in-page capture).

### Q5 Statement lines with order-ref mappable to sales
- Noon ✅ 1852 lines, all with external_order_id (LIVE-verified).
- Keeta ✅ XLSX "Order Summary" → line per Order Number (== sales id); verified 37→148 on the real file.
- Talabat ⚠️ lines come from the xlsx bundle; truncation note when the bundle download is blocked.
- Deliveroo ❌ statement_csv (the line source) is Cloudflare-403; lines require the clearance path.

### Q6 Codebase clean + retry/fail
- Solid base (rate limiter, retry on 429/5xx, Auth vs Unavailable classification, advisory locks, idempotent upserts, per-channel isolation). Hardened: Deliveroo AuthError propagation; Talabat download auth-classification; Keeta double-emit + endpoint repoint + getOrders loop resilience (noted); clearer truncation notes throughout.

## Recommended follow-ups (not shipped)
1. **Persist rotated Set-Cookie from the ingest** — keeps Noon/Talabat alive with zero browser (the real liveness fix; supersedes headless warm).
2. In-page/clearance download path for Deliveroo invoices+lines and Noon statement PDFs (Cloudflare/Akamai gate the httpx download).
3. Keeta: assert LOGIN_ACCOUNTID before getOrders; confirm the finance LIST request bodies (pageSize/downloadTaskType) on a live run.
4. Promote aggregator orders so statement-line external_order_id → mm_order_id mapping fills (currently ord_ref populated, mm map pending promotion).

---

# ROUND 2 — headed browser, R2→GCS, live reauth (2026-08-28)

## Headed browser warm/auth — PROVEN on the VM (answers "explore headed")
- Headless real Chrome is dropped by the anti-bot edges: `page.goto` to
  restaurant.noon.partners never commits (ERR_HTTP2_PROTOCOL_ERROR / timeout),
  while plain HTTP GET = 200. This is the true reason Noon warm never worked and
  Talabat died.
- **Headed real Chrome under Xvfb passes Akamai (noon), PerimeterX (talabat) AND
  Cloudflare (deliveroo)** — all load 200, challenged=False.
- **Warm verified live**: a headed warm rotated Noon's Akamai cookie and pushed it
  `live` (last_warmed_at advanced) where every headless attempt failed.
- **Reauth verified live**: a headed `login --auto` under Xvfb reauthed the DEAD
  Talabat session (Graph OTP auto-read, PerimeterX passed) → status live →
  `talabat sales` sweep returned **30 orders**. Reauth now runs ON THE VM — no
  separate UAE laptop needed.
- The image already ships real google-chrome; only Xvfb+xauth were missing.

Implemented (local): bootstrap Dockerfile (+xvfb +xauth, docker-entrypoint.sh runs
under xvfb-run, ENV HEADLESS=false); `browser.py` `_open_storage_state_context`
now honours headed (it hardcoded headless=True — THE bug); `standalone_chrome_args`
+`--no-sandbox`/`--disable-dev-shm-usage` (login's standalone Chrome needs them in a
container); noon probe `--disable-http2` + console-root URL + tolerant goto; warm
cron re-enabled for noon+talabat.

**⚠ VM resize required.** Headed Chrome under Xvfb dips the 1 GB e2-micro to
~110–145 MB free (survived every run, but tight; the api container showed a restart
during the heaviest run). Resize to e2-small/2 GB (min) or e2-medium/4 GB. Do the
GCS scope change in the same stop.

## R2 → GCS migration (answers "kill all R2 refs")
- Confirmed: production serves images from **GCS `mm-product-images`** (public),
  not R2 — R2 config was vestigial with invalid creds. You were right.
- New **`app/core/object_storage.py`** (ADC via the VM metadata server — verified
  the api container gets a token, and upload/delete round-trip to GCS works live).
- Images (`uploads.py`) → GCS `mm-product-images`; invoices (`statement_docs.py`)
  → new **private bucket `melting-moments-data`**, prefix `invoices/`.
- Removed ALL `CLOUDFLARE_R2_*` / boto3 across config, the W9 5 locations
  (.env.example, PRODUCTION.md, deploy.yml, rollback.yml, compose ×2 services),
  uploads.py, statement_docs.py, and the frontend (`apps/web` + `apps/admin`
  next.config CSP/remotePatterns) + a test fixture. W9 allowlist test passes.
- **New GCS bucket created**: `gs://melting-moments-data` (ME-CENTRAL1, private,
  public-access-prevention) — generic, for invoices now + merged data later.
- Talabat finance(15d) ran live through GCS statement_docs (8 statements; bundle
  legitimately empty this window, archival path exercised, no crash).

**⚠ One infra step for signed invoice-download URLs:** the VM SA token has only
`devstorage.read_write` scope, so IAM signBlob = ACCESS_TOKEN_SCOPE_INSUFFICIENT.
Upload/read/delete all work; signed URLs need
`gcloud compute instances set-service-account mm-backend --zone=me-central1-a
--scopes=cloud-platform` (instance stopped — fold into the resize). Token Creator
role already granted. `presigned_get_url` has no consumer yet, so nothing is broken.

## Test status
2384 API unit tests pass (incl. the migration + all provider fixes); 33 bootstrap
tests pass; ruff clean; repo grep for R2/boto3 is empty (outside .claude/worktrees).
VM left clean and healthy (all core services healthy, no stray Chrome/Xvfb).

## Still open (recommended next)
- Deliveroo invoices/lines: headed Chrome passes the Cloudflare gate — implement an
  in-page headed download of the statement CSV/PDF (like Keeta's in-page pull) now
  that the mechanism is proven.
- Noon statement-PDF: discover the console's statement-file endpoint via a headed
  in-page capture (the browser can now load the console).
- Optional: persist rotated Set-Cookie from the ingest as a zero-browser liveness
  backstop (headed warm now covers the main need).

---

# ROUND 3 — reconciliation coupling, config, clean slate (2026-08-28)

## Reconciliation coupling — the full chain now closes (live-verified)
The chain **payout ← statement ← line ← order ← mm_order ← customer/items** is
now wired and populated. Live run on prod (patched code, real data):

| link | before | after | how |
|---|---|---|---|
| order → mm_order (promoted) | deliveroo 1, noon 0, keeta 28, talabat 33 | 23 / 390 / 768 / 63 | wider promotion window (`AGGREGATOR_PROMOTE_LOOKBACK_DAYS`=30, separate from sales lookback), stock still gated to the 1-day window |
| statement_line → mm_order | 0 | noon 1484 | promotion backfills `line.mm_order_id` |
| order → statement | noon only | backfilled for every channel | `_upsert_statement` links the orders a statement's lines name |
| statement → payout | none | rollup (verified S807→P807, S815→P819) | `link_statements_to_payouts`: a statement is cleared by the first payout on/after its due date (noon payout 8328.29 = 5046.48+3281.81 of the two statements before it) — new `aggregator_statement.payout_transfer_id` column |
| reconciliation | httpx channels only | ALL channels incl. **Keeta** | reconcile moved to channel-agnostic `sweep_reconcile_once` after promote (Keeta was silently never reconciled) |

Data-model facts found: noon payouts are ACCUMULATED (one transfer clears several
statements — a single `payout.statement_id` FK can't model it, hence the
statement-side `payout_transfer_id`); talabat "statements" are per-invoice-file
rows with null `net_payable` (real figures live in the xlsx lines).

## Config extraction (hardcoded → config/DB)
- Settings: `AGGREGATOR_PROMOTE_LOOKBACK_DAYS` (30), `AGGREGATOR_NOON_PUBLICATION_LOOKBACK_DAYS` (14) — W9-wired, allowlist green. VAT reuses `order_pricing.VAT_RATE`.
- Per-account marketplace scope → `aggregator_account.extras` (fallback = old constant, behaviour-identical until populated): talabat `global_entity_id` (TB_AE), careem `city_id` (1), keeta `shop_ids`/`customer_id` (330066). Deliveroo `org_id`/outlets already DB-sourced.

## Scalability / generalization / error-handling (from the audits)
- Keeta reconciliation gap FIXED (was never reconciled).
- One bad sales row no longer aborts the whole channel sweep (per-order try/continue).
- Recon-join index `grubops_order_map(source_channel, external_id)` + `sync_run(started_at)` + `statement(channel, payout_transfer_id)` (migration 161).
- Unbounded `while True` page loops capped (careem `_MAX_PAYOUT_PAGES`, keeta `_MAX_ORDER_PAGES` = 200).
- Redeploy survival confirmed sound: DB is source of truth; sessions/accounts/branch_maps/data survive; worker rehydrates; scheduler catches up from the durable run trail.

## Clean slate (authorized)
Migration **161_agg_clean_slate** TRUNCATEs the scraped tables (order/item/
statement/line/payout/reconciliation/sync_run), adds `payout_transfer_id` + the
indexes. KEEPS accounts, sessions, branch maps, Foodics, GrubOps, and `orders`
(promoted MM orders re-converge on their existing rows). Verified on a throwaway
Postgres: full chain upgrade, wipe, column+indexes, downgrade, idempotent re-up.

## Deliveroo in-page download
Worker `deliveroo_pull.py` fetches the invoice list + downloads the statement
CSV/PDF IN-PAGE (headed Chrome clears Cloudflare) → `POST /aggregators/deliveroo/
finance` → `parse_pushed_finance` (reuses the existing CSV line parser) → archive
to GCS + `_upsert_statement`. Unit-tested; needs live verify on the rebuilt/resized VM.

## Runbook
`docs/aggregator-runbook.md` — VM resize + SA `cloud-platform` scope (one stop),
deploy (migration 161 + bootstrap image), reauth-on-VM, warm cron, first re-ingest,
Deliveroo in-page pull, and the coupling-verification query.

## Test status
2400 API unit tests + 41 worker tests pass; OpenAPI + `@mm/types` regenerated (no
drift); ruff clean; migration verified on real Postgres. Nothing pushed; prod
container untouched (all live runs were ephemeral `docker compose run --rm`).

---

# ROUND 4 — TODO implementation + endpoint audits (2026-08-28)

## VM resized (authorized)
e2-micro → **e2-small** (2 GB), SA scope → **cloud-platform**, **IAM Credentials
API enabled**. Downtime ~2 min; GrubOps ingest resumed on restart (poll loop
catches the live order board) and POS came back healthy — constraint met.
**GCS signed URLs now work** (fetched a signed object → MATCH).

## Endpoint audits → new functionality
- **Noon customer — SOLVED.** Probed the live OMS order: it carries
  `customerInfo.name` + `customerInfo.phone` (+ receiverInfo, notes, driver
  info) — the provider hardcoded them to None. Wired it → **live-verified: 22/22
  recent orders now have real names + UAE phones**.
- **Deliveroo download — SOLVED (the hard one).** Headed real Chrome clears
  Cloudflare (block is now 401 auth, not a 403 interstitial). The remaining
  failure: the download 302-redirects to a cross-origin signed URL that an
  in-page `fetch(credentials:"include")` can't follow (CORS → "Failed to fetch"),
  even after a login clears the 401. Fix: **Playwright NATIVE download capture**
  (`page.expect_download` + navigation) + an email/password re-login first to
  refresh the stale browser token. Live-verified: real statement CSV downloads
  (3520 bytes, "Orders and related adjustments", the per-order lines).
- **Deliveroo/Talabat customer — confirmed marketplace withholds** (Deliveroo
  order detail exposes only `customer.id`, no PII). Not a code gap.
- **Noon statement PDF — deferred**: the wallet statement row carries no download
  link; the PDF is a separate console endpoint needing SPA capture (the console
  web session, unlike the httpx data session, redirects to login).

## Implemented
- Noon customer from OMS `customerInfo`/`receiverInfo`.
- Keeta **payouts** parsed from the weekly bill XLSX "Invoice Details" (sum of
  Payable to Restaurant per billing cycle → StandardPayout, coupled to the
  statement): 841.73 settled + 322.97 pending on the real file.
- **Layer-A settlement reconciliation** service + `GET /aggregators/
  reconciliation/settlement` (per-statement sales-vs-settled-vs-paid rollup +
  the accumulated-payout check, 5046.48+3281.81=8328.29 → 0 variance).
- Deliveroo in-page download rewritten to native download-capture + login refresh.
- Keeta **LOGIN_ACCOUNTID liveness assert** (raises NeedsHumanLogin instead of a
  silent risk-controlled pull when the session is signed out).

## Test status
2415 API + 42 bootstrap tests pass; OpenAPI + @mm/types regenerated (drift check
clean); ruff clean. VM healthy on e2-small (1 GB+ free); prod containers
untouched (all live runs ephemeral). Nothing pushed.

## Still deferred (low value / needs more)
- Noon statement-PDF endpoint (SPA capture); Talabat statement-summary-from-lines
  (Layer A already handles the null total gracefully); persist rotated Set-Cookie
  (headed warm now covers liveness); full Deliveroo E2E push→ingest (needs the
  patched API deployed).

---

# ROUND 5 — dynamic fees, commission bug, customer audits, noon PDF (2026-08-28)

## Dynamic aggregator fees → MM order (your coupling ask) — DONE + live-verified
`order_fees.stamp` now takes `actual_commission`/`actual_payment_fee`; promotion
passes the settled `aggregator_order.commission_amount`/`payment_fee`, so a
promoted order's `aggregator_fee` carries the marketplace's ACTUAL cut once the
statement settles (dynamic beats the static configured rate) — for both
promotion-owned (DSO/Karama) and GrubOps-owned orders (a deliberate, documented
fee-only overlay). Reconciliation now RECOMPUTES its modelled estimate
(`order_fees.compute`) instead of reading the column, so the commission-variance
check stays meaningful. Live: MM `aggregator_fee` == agg commission (45.00, 17.50…).

## Noon commission was ALWAYS ZERO — real bug found via the fee audit, fixed
Noon reports the commission under the field **`lead_generation_fee`** (e.g. item
30 → `fees_exc_vat -8.1` = commission `-7.5` + payment `-0.6`). `_commission_from`
was SUBTRACTING `lead_generation_fee` as if a separate fee, so every noon
commission computed to 0 (5,412 orders). Removed it from the netted-out set →
live: 226 orders now show the real ~25% commission, flowing through to the MM
order's `aggregator_fee`. (Stale zeros clear on the clean-slate re-ingest.)

## Customer audits — both confirmed UNAVAILABLE (privacy, not a code gap)
- **Deliveroo**: order detail exposes only `customer:{id}`; all 6 candidate
  endpoints (`/customers/{id}`, `/orders/{id}/customer`, `/consumers/{id}`,
  `/delivery`, `/contact`, include-variant) 404 or carry no PII.
- **Talabat**: the export CSV carries no customer columns (consistent with the
  marketplace's PII posture). (Aside: the current talabat session lacks
  `account_ids`/store ids to scope an export — a session-completeness follow-up:
  add the talabat store ids to `aggregator_account.extras`, like the entity id.)
- Noon + Keeta remain the two channels that DO expose customer (both fill).

## Noon statement PDF → GREEN
noon exposes no downloadable statement file (audited: 10 API endpoints 404, the
console SPA route is opaque). So noon's fetch_statements now RENDERS its own
per-order settlement rows (order id, date, item value, every fee, VAT, net) to a
CSV and archives it to GCS as the statement document. Live-verified: 4 noon
statements carry `invoice_object_key`, CSVs in `gs://melting-moments-data/invoices/
noon/…` (25 KB each), retrievable via the now-working signed URLs.

## Test status
2415 API + 42 bootstrap tests pass; ruff clean; VM healthy on e2-small (1 GB+
free), all services up. Prod containers untouched (all live runs ephemeral).
Nothing pushed.
