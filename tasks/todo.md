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
