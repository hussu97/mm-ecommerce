# Integrator Capabilities — What We Support Per Aggregator, and How

One reference for every integrator MM talks to: what each one can do (read the
menu, read hours, create/delete items, map to MM), **how** to do it (the endpoint,
the code entry point, the admin action), and the **verification status** of each
capability — verified live, gated-until-verified, or blocked on a headed browser.

Nothing here is aspirational. Each "verified" row was confirmed against the real
integrator on the date shown; each "pending" row says exactly what is missing and
how to finish it. Sources: `docs/aggregator-catalog-hours-sync-audit.md`,
`docs/aggregator-catalog-hours-sync-audit.md` (verification log), and the code
under `apps/api/app/services/aggregators/` + `apps/api/app/services/providers/`.

---

## 0. Capability matrix (at a glance)

**Deployed to production (main) + verified live on the VM 2026-09-01.** Migration
head 172; the aggregator-worker image carries the Keeta menu job. The menu/hours
readers below marked ✅ were run against the **deployed** VM against real sessions,
not just captured shapes.

| Integrator | Menu read | Hours read | Item create | Item delete | Order/sales ingest |
|---|---|---|---|---|---|
| **Foodics** (master) | ✅ verified | n/a¹ | ✅ verified | via Foodics | ✅ (existing) |
| **Careem** | ✅ verified² | ✅ verified | ✅ verified³ | ✅ verified³ | ✅ (existing) |
| **Talabat** | ✅ verified | ⏸ separate service | ⏸ import-based | ⏸ | ✅ (existing) |
| **Noon** | ✅ verified | ✅ verified⁵ | ⏸ RMS-doc | ⏸ | ✅ (existing) |
| **Keeta** | ✅ built⁴ | ⏸ endpoint elusive | ⏸ write | ⏸ | ✅ (existing) |
| **Deliveroo** | ⏸ headed⁶ | ⏸ headed | ⏸ headed | ⏸ | ✅ (existing) |

¹ Foodics carries no aggregator hours — hours are per-marketplace.
² Careem menu read was **fixed twice**: it omitted the required `merchantId` (400),
  and a parent/promo category 404 was aborting the whole read — now isolated
  per-category (found running the deployed reader vs Barsha live).
³ Verified by a controlled create-then-delete on a live outlet (created INACTIVE,
  deleted, re-read confirmed gone).
⁴ Keeta menu is read by the **headed worker** (H5guard/mtgsig — no server call):
  endpoints + shapes verified live; parser unit-tested; worker fetch + API ingest
  built and **deployed**. The KEETA_MENU job runs on the VM (execute-verified — the
  first run surfaced a LOGIN_ACCOUNTID timing bug, now fixed with a 6s SPA-boot wait
  like the orders job); the menu snapshot populates on its next un-preempted run.
⁵ **Noon hours — verified server-side.** `POST /_food-restaurant/restaurant/outlet/details`
  `{outletCode, version:0}` → `data.schedule.periods` (day-index keys, comma-joined
  for shared days). Day origin **proven by the response's own `periodsDesc`**:
  0=Mon…6=Sun, so MM weekday = `(noon_day+1)%7`. `parse_noon_hours`, unit-tested.
⁶ Deliveroo's menu editor is a separate login **and** behind Cloudflare (bot
  challenge — not bypassed); its endpoint must be captured on the worker's real
  Chrome (§6).

✅ = verified live and shipped · ⏸ = not yet, with the exact reason + path below.

---

## 1. How the catalog & hours layer is built (shared architecture)

- **Readers** (`app/services/aggregators/menu_readers.py`) translate each
  integrator's menu/hours into the channel-neutral `NormalizedMenu` /
  `NormalizedHours` (`menu_normalized.py`). MM's own catalogue is translated into
  the *same* shape, so a diff is one comparison of two `NormalizedMenu`s.
- **Providers** (`app/services/providers/*_provider.py`) speak each integrator's
  protocol, replaying the **same authenticated session the sales ingest uses** —
  TLS-impersonated (`request_json` / `request_raw` on `aggregator_base`) so
  anti-bot (PerimeterX/Akamai) passes without a browser.
- **Diff** (`catalog_diff.py`) produces the drift the admin reads; **mapping**
  (`catalog_mapping.py`) records `external_item_map` rows (item by name, option by
  name+price); **writers** live on the providers, gated.
- **The sweep** (`catalog_sync.run_catalog_sync_once`) refreshes menu+hours,
  refreshes mapping proposals, and stores drift, per-target isolated.

### Feature flags (all default OFF/0; W9 five-place)
| Flag | Effect |
|---|---|
| `CATALOG_SYNC_READ_ENABLED` | Turns on live reads (opens marketplace sessions). Off = drift served from last snapshot; refresh 503s. |
| `CATALOG_SYNC_ENABLED` | Master gate for **every write** (creates, price/hours pushes). Off = writers 503. |
| `CATALOG_SYNC_ENFORCE_PRICE_PARITY` | Foodics price-tag price must equal the product price. |
| `CATALOG_SYNC_SWEEP_MINUTES` | Cadence of the autonomous sweep (0 = off). |

### Autonomy
`run_catalog_sync_scheduler_forever` (in `catalog_sync.py`) mirrors the rolling
sales-refresh loop and is registered as a third child under the ingest's leader
election (`ingest.run_aggregator_schedulers_forever`) — one API slot ticks, and a
blue/green cutover hands it over for free. DB-backed boot catch-up uses the
freshest snapshot's `fetched_at` as the trail. Enable with
`CATALOG_SYNC_SWEEP_MINUTES > 0`.

### Admin surface (`apps/admin` → Catalog Sync, `/api/v1/catalog-sync/*`)
- **Drift report** per branch/target (`GET /drift`).
- **Refresh live read** (`POST /refresh`, gated).
- **Resolve mappings** per target (`POST /mappings/resolve`) — approves the exact
  name/name+price matches from the last read.
- **Create item** (`POST /items`, gated, dry-run default).
- **Weekly hours** GET/PUT (`/branches/{id}/hours`) — MM's canonical schedule, the
  desired side of the hours diff.
- Product/category **sync toggles** (`PUT /products|categories/{id}/sync`).

---

## 2. Foodics — the master for the two integrated branches

**Role.** Sharjah (K001) and Barsha (B001) are Foodics-integrated: their aggregator
menu on *all five* marketplaces IS the Foodics **"Grubtech" price tag** (aggregator
price) fed by the **"Grubtech" group** (membership). So for these branches you never
write a marketplace directly — you write Foodics, and Foodics syncs everywhere.

**Auth.** Console session (`foodics_provider`), Cloudflare + reCAPTCHA login.

**Menu read.** `list_price_tag_products(GRUBTECH_PRICE_TAG_ID)` →
`GET /core-api/listing?url=/price_tags/<pt>/products` — `pivot.price` is the
aggregator price. Reader: `_read_foodics_menu`. ✅ verified.

**Create (✅ verified live 2026-09-01).** Console CRUD verbs read from the SPA's own
client: `getting`/**`creating`**/`updating`/`deleting` (NOT `inserting`). Create =
`POST /core-api/creating {url:"/products", payload:{…}}`:
- product joins its **Grubtech category subgroup** (9 subgroups, keyed by MM
  category name — `FOODICS_GRUBTECH_SUBGROUPS`);
- **price-tag price = base price** (strict parity — confirmed in the wild);
- `category_id`, `tax_group_id` (UAE VAT `a03ed56a…`), `pricing/selling/costing =
  1/1/2` are echoed or Foodics rejects the product.
- Code: `foodics_provider.create_product` / `add_product_to_grubtech` /
  `set_price_tag_product_price` / `category_id_by_name`.

**How to create.** `POST /api/v1/catalog-sync/items {product_id, target:"foodics"}`
(dry-run by default; `dry_run:false` + `CATALOG_SYNC_ENABLED` applies). The
marketplaces then show the item on their next read and `resolve_menu` records each
channel's `external_item_map` automatically; the Foodics row is recorded inline.

---

## 3. Careem — full REST, everything verified

**Auth.** Bearer session under `partners.careem.com/api/saturn-ext` (the sales
ingest's session). Company `1026653`, brand `1029671`. Outlets: Silicon Oasis
`1069463`, Barsha `1067984` (Dubai); Al Majaz `1087801` (Sharjah) is `403` for this
session. Portal login (Chrome) is `partners.careem.com`.

**Menu read (✅ — fixed).** Base
`/v1/careem/1/company/{c}/brand/{b}/outlet/{o}`:
- `catalog-catalogs?merchantId={outlet}` — **the `merchantId` param is required**;
  without it, 400. (This was a shipped bug — the reader omitted it — fixed
  2026-09-01.)
- `catalog-categories?catalogId={cat}` — a **flat list** `[{id, name, …}]` (not
  `{subCategories}`).
- `catalog-products?categoryId={cat}` — `{products:[{id, name, defaultPrice,
  status, customizationGroups, …}]}`; price=`defaultPrice`, avail=`status=="ACTIVE"`.
- Reader: `_read_careem_menu` / `parse_careem_catalog`.

**Hours read (✅ verified live 2026-09-01).** `GET
{base}/food-outlet-operational-hours` → `[{day:1..7, active:0|1,
shifts:[{start_time:"HH:MM:SS", end_time:"HH:MM:SS"}]}]`. **Day origin confirmed
against the Store Manager bundle's own labels: `day1=Sunday … day7=Saturday`** — so
MM `weekday = day − 1` (MM is 0=Sunday…6=Saturday). `active:0` = closed. Reader:
`_read_careem_hours` / `parse_careem_hours`, registered in `_HOURS_READERS`.

**Create + delete (✅ verified by controlled create-then-delete).** Confirmed on a
live outlet — created one INACTIVE product, captured the request, deleted it,
re-read to confirm gone:
- `POST {base}/catalog-products {name, nameLocalized:{en,ar}, defaultPrice,
  status:"INACTIVE"|"ACTIVE", catalogId, categories:[<categoryId INT>]}` → `201 {id}`.
  **`categories` is a list of integer ids** — the API 400s on `{id:…}` objects.
- `DELETE {base}/catalog-products/{id}` → `204`.
- Code: `careem_provider.create_product` (defaults INACTIVE) / `delete_product`.

**How to create.** `POST /api/v1/catalog-sync/items {product_id, target:"careem",
branch_id}` — resolves the MM category to the Careem category id, creates INACTIVE,
records the mapping (`_create_on_careem`).

---

## 4. Talabat — read verified; write is import-based (pending)

**Auth.** DeliveryHero bearer under
`vendor-api-gdp-ae.me.restaurant-partners.com/api/5/platforms/TB_AE` + the
`x-global-entity-id`. Vendors (outlets): `711571`, `728173`, `793319`.

**Menu read (✅ verified).** `/vendors/{v}/catalogs` → `{catalogs:[{id, name,
categories:[{id,name}]}]}` → `/vendors/{v}/catalogs/{c}/categories/{cat}/products`
→ `[{id, name, unitPrice, availability:{available}, active, isVariation,
productOptionIds}]`. Sizes are **separate products**, so there is no option layer.
Reader: `_read_talabat_menu` / `parse_talabat_catalog`.

**Hours read (⏸ pending — separate service).** Every schedule path on the menu API
404s ("no Route matched"): `/vendors/{v}`, `/availability`, `/opening-times`,
`/schedules`, `/schedule`, `/opening-hours`, `/special-days` (probed 2026-09-01).
DeliveryHero manages availability on a **different microservice**; the endpoint must
be captured from the portal's network (anti-bot → headed capture on the VM) before
a reader can be trusted.

**Create + delete (⏸ pending — import-based).** The per-item `POST` to the read
path returns **405 Method Not Allowed** (probed 2026-09-01), so DeliveryHero menu
writes are **not** a plain REST create — they go through a **catalog import** (bulk
job), not confirmed live. The create dispatch raises a clear "not yet verified" for
Talabat. **To finish:** capture the portal's actual add-item request (or the DH
catalog-import job shape) and implement `talabat_provider.create_product` against
it, then verify with a controlled create-then-delete (the Careem template).

---

## 5. Noon — read verified; write is an RMS menu-document rewrite (pending)

**Auth.** RMS session under `restaurant.noon.partners` with the
`n-restaurantcode`/`x-project`/`x-locale` headers. Restaurant code
`R5967280642376629909871448A`; `idPartner` 135208. There is a 5th portal outlet
`MLTNGMQ677` not yet in the branch map.

**Menu read (✅ verified).** `GET /_food-restaurant/menu/list` → the menus (the
"Melting Moments" menu `M3497661938091593085106254A` is MM-managed; the "Ext.
grubtech" menus are Foodics-fed) → `POST /_food-restaurant/menu/details {menuCode}`
→ `{items:[{itemCode, nameEn, price, isActive, isOos, categoryCode, itemType}],
categories:[…], modifiers:[{options:[{itemCode, price}]}]}`. avail = `isActive AND
NOT isOos`; options resolve by (name, price). Reader: `_read_noon_menu` /
`parse_noon_menu`.

**Hours read (⏸ candidate only).** Read-discovery 2026-09-01: `restaurant/list` →
200 (partner list); `restaurant/details` → **405 on GET (exists, POST-only)** — a
candidate for the hours/timing but its body + payload are unconfirmed; every plain
`store/hours|timing|schedule` path 404s. **To finish:** confirm `restaurant/details`
(POST) contents (or the OMS host) — likely needs the portal network capture.

**Create (⏸ pending — document rewrite).** Noon's menu is edited as a **document**
(the whole menu is saved back), not a per-item REST create, so a create is riskier
than Careem's and was not live-verified. The dispatch raises "not yet verified" for
Noon. **To finish:** capture the RMS menu-save request from the portal, implement
the item-add against the menu document, verify with a controlled create-then-delete.

---

## 6. Keeta & Deliveroo — the headed worker (Keeta menu now built)

Neither exposes a server-callable menu API, so they run in the **headed
aggregator-worker** (headed Chrome under Xvfb, `apps/aggregator-bootstrap`) — the
same worker that already reads Keeta orders and Deliveroo finance in-page.

### Keeta menu read — BUILT (endpoints verified, parser tested)

Keeta signs every menu XHR with an in-page `mtgsig` (H5guard), so it is read the
identical way `keeta_pull.fetch_keeta_orders` reads orders — the page's own signed
`fetch` via `evaluate_in_page`. Verified live 2026-09-01 (merchant.mykeeta.com/m/web/product):
- `POST /api/sailorProduct/shopCategory/r/listShopCategory {shopId}` → categories
  (each with `availableTimeDTO`);
- `POST /api/sailorProduct/spu/r/listSpu {shopId, pageNum, pageSize}` →
  `{spuList:[{id, name, status, shopCategoryIdList, skuList:[{price, currency}]}]}`.
  45 products matched MM's catalogue; sizes are separate SPUs.

Shipped: `keeta_pull.fetch_keeta_menu`, `warm.pull_keeta_menu_in_page`,
`push.push_keeta_menu`, `JobKind.KEETA_MENU` (+ timeout +
`WORKER_KEETA_MENU_INTERVAL_HOURS` cadence in the daemon), the API's
`POST /aggregators/keeta/menu` → `catalog_sync.store_worker_menu` (keeta menu
snapshot), and `menu_readers.parse_keeta_menu` / `_read_keeta_menu` (registered).
The parser is unit-tested against the real shapes; the browser step follows the
proven `keeta_pull` pattern and awaits a live worker run to execute-verify — the
same status `keeta_pull` itself had when authored.

**Keeta hours / create — the remaining path.** Hours: the shop-level weekly
schedule endpoint wasn't isolated (the settings page is telemetry-flooded); the
category `availableTimeDTO.values` (7 per-day ranges, already in the menu push) is
a candidate but its day-origin/semantics need confirming before it drives a
branch's open/close. Create is a menu **write** (`sailorProduct` mutation) —
discoverable the same in-page way, but a live storefront write is not shipped
unverified.

### Deliveroo — separate login + Cloudflare

Deliveroo's menu editor is a **separate login** (`/login?return_to=/menus/…`) and
the hub is behind a **Cloudflare bot challenge** that does not clear in this
environment. So its menu endpoint must be captured on the worker's real Chrome
(where `deliveroo_pull` already carries `cf_clearance` for finance), then a
`DELIVEROO_MENU` job built exactly like the Keeta one above.

---

## 7. Mapping (all channels) — one table, approval-gated

Every channel's items/options/categories map to MM through the single
`external_item_map` (system, external_ref → product/option/category), approval-gated.
- The ingest proposes rows from scraped order lines; `resolve_menu` proposes from a
  full menu read (item by exact name, option by name+price).
- `POST /catalog-sync/mappings/resolve?target=` (admin "Resolve mappings" button)
  approves the confident exact matches from the last snapshot; genuine variants stay
  as proposals for a human.
- **Verified resolution (2026-09-01):** Talabat 46/46 products (no options); Noon
  43/45 products + 90/108 options — residue is two Noon size/name variants + the
  plural/singular box-filling options.
- Branch maps (`aggregator_branch_map`) are complete for all five channels.

---

## 8. What's left, precisely

| Item | Blocker | How to finish |
|---|---|---|
| Talabat create/delete | Import-based; per-item POST 405s | Capture the DH catalog-import job; implement + controlled create-then-delete |
| Noon create/delete | Menu is a document rewrite | Capture the RMS menu-save; implement + controlled create-then-delete |
| Talabat hours read | On a separate DH service | Headed portal capture of the availability endpoint |
| Noon hours read | `restaurant/details` (POST) unconfirmed | Confirm its payload/contents (or OMS host) |
| Keeta menu read | H5guard (in-browser signature) | **Built** — awaits one live worker run to execute-verify (§6) |
| Keeta hours read | Shop-schedule endpoint not isolated | Capture it on the settings sub-tab, or confirm `availableTimeDTO` semantics |
| Keeta / Deliveroo create | Live storefront write, headed | Discover the write endpoint in-page, then a gated `JobKind` |
| Deliveroo menu/hours read | Separate login + Cloudflare | Capture the menu endpoint on the worker's real Chrome; `DELIVEROO_MENU` job (§6) |
| Live Talabat/Noon create verification | The environment's write-guard blocked the live create-probe | Run the controlled create-then-delete when permitted |

Everything not in this table is verified and shipped (or, for Keeta menu, built +
unit-tested with the browser step pending a live worker run).
