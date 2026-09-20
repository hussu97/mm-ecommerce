# Printable Menu PDF for Menu Groups

Generate & download a print-ready PDF menu per menu-group root, realtime from the
DB, in English or Arabic. Counter (branch) roots brand by the counter legal
entity (Barsha → Attibassi / Najm AlShamal); the integrator root = Grubtech
aggregator menu (prices = MM base_price parity). QR = branch WhatsApp.

## Decisions (confirmed with user)
- Missing product photos → graceful no-photo layout (guaranteed floor). Real
  photos used where present. No copyrighted-image scraping, no photoreal AI
  (unavailable here). Tasteful SVG category icons added. Admin upload path stays
  so photos flow in automatically later.
- Grubtech price → MM `base_price` (parity), no live Foodics call.
- Ship flow → build, render EN/AR samples, **show user, wait for approval before
  pushing to main.**

## Stack
- Server-side: Jinja2 (present) → HTML → WeasyPrint (new) → PDF. QR via segno
  (new, pure-Python, inline SVG). Product images fetched with httpx (present),
  inlined as data URIs. Bundled OFL fonts (Poppins, Marcellus, Almarai-Arabic).
- Endpoint: `GET /menu-groups/{group_id}/pdf?lang=en|ar`, `require("catalogue.manage")`,
  returns `application/pdf` attachment. Blocking render via `asyncio.to_thread`.
- Admin: `menuGroupsApi.downloadPdf` via `downloadBlob`; EN/AR buttons in header.

## Build steps
- [ ] Bundle OFL fonts + license under `app/services/catalog/menu_pdf/fonts/`
- [ ] `menu_pdf/builder.py` — MenuDocument dataclass assembled from a root group
      (tree walk, options/prices in Python, brand via tax_identity_service,
      branch phone, EN/AR strings)
- [ ] `menu_pdf/theme.py` — brand theme by legal-entity reference
- [ ] `menu_pdf/icons.py` — inline SVG category icons + WhatsApp QR helper
- [ ] `menu_pdf/templates/menu.html.j2` — the layout (theme-aware, RTL-aware)
- [ ] `menu_pdf/render.py` — Jinja2 → WeasyPrint bytes; concurrent image inline
- [ ] Sample fixture + render script; render EN/AR for Attibassi + MM + Grubtech
- [ ] **CHECKPOINT: send samples to user, get approval**
- [ ] Endpoint in `menu_groups.py`
- [ ] Deps: add weasyprint + segno to pyproject; `uv lock`
- [ ] Dockerfile runner stage: Pango/fontconfig/gdk-pixbuf apt libs
- [ ] Admin binding + buttons
- [ ] Tests (builder unit; endpoint smoke)
- [ ] ruff format + check; run affected tests; regenerate types if schema touched
- [ ] Push to main; verify deploy green; verify on prod

## Notes / guardrails
- uv.lock must be regenerated with pyproject (Docker build `--locked` fails on drift)
- No new env vars (fonts bundled, no external service) → no 5-place secret churn
- No DB schema change → no migration
- Commit author: Hussain Abbasi <h_abbasi97@hotmail.com>, no Co-Authored-By

---

# VM CPU BAU reduction (2026-09-20)

Keep every existing poll/reconcile frequency and all functional sync paths while
removing repeated no-op database and recipe-catalog work found in the production
CPU profile.

- [x] Gate missing-recipe retries on a durable recipe-catalog generation; bump it
      transactionally on activation and stamp each attempted source event.
- [x] Batch-load GrubOps order maps once per tick and skip savepoints/commits for
      summaries whose status is already ingested.
- [x] Backfill the two approved Lotus Cookie Melt GrubOps mappings with brand id
      `6922ff03323715175fede66b`; reject future approved GrubOps rows without scope.
- [x] Add focused regression tests and run migration/static checks.
- [x] Commit with the repository's required author.

---

# VM CPU BAU reduction — second pass (2026-09-20)

Keep every production scheduler and client polling cadence unchanged while
removing redundant payload, query, and logging work.

- [x] Change the aggregator daemon's 5-minute heal check to the existing
      status-only endpoint; preserve the authoritative liveness policy and skip
      browser relogin for API-refreshable channels.
- [x] Collapse the POS app's two 20-second pending/active reads into one
      `open_only=true` read and partition the identical result locally.
- [x] Suppress successful `/ping` request logs and INFO chatter from HTTP/PDF
      libraries while retaining errors and business-request access logs.
- [x] Add focused backend and POS regression tests.
- [x] Run format/static checks and affected test suites in both repositories.
- [ ] Commit with the required author, push both changes directly to `main`, and
      verify the backend deployment and production container health.

## Review

- Backend: 3,388 unit tests passed (12 skipped); aggregator worker: 199 passed;
  focused CPU-path tests: 47 passed; Ruff and generated-contract freshness pass.
- POS: complete `swift test` suite passed with zero failures; existing Swift 6
  warnings remain unrelated to this change.
- Production deploy/health verification pending the direct-to-main pushes.

---

# Unified admin customers (2026-09-20)

- [x] Add a transactionally rebuilt, dirty-tracked customer cache sourced from
      customer-bearing orders and registered accounts; exclude anonymous counter
      checks and deduplicate only on name plus a shared email or phone.
- [x] Normalise source phones into E.164 and persist the detected country beside
      them across account, marketplace-ledger, and custom-order write paths.
- [x] Replace the registered-user customer endpoint with cached customer metrics
      and a scoped order-history endpoint.
- [x] Rebuild the Customers admin table and its order-history modal; regenerate
      OpenAPI TypeScript contracts.
- [x] Add focused regression coverage, run API/admin checks, and commit the
      coherent feature with the required author.
