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
| **Noon** | ✅ verified | ✅ verified⁵ | ✅ verified⁸ | ✅ verified⁸ | ✅ (existing) |
| **Keeta** | ✅ built⁴ | ✅ verified⁹ | ✅ verified⁷ | ✅ verified¹⁰ | ✅ (existing) |
| **Deliveroo** | ⚙ coded⁶ | ⚙ coded⁶ | ⏸ headed | ⏸ | ✅ (existing) |

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
⁶ **Deliveroo menu + hours — DECODED (2026-09-01), coded + tested, not yet deployed.**
  Earlier belief (separate menu login) was wrong: the Partner Hub session
  **auto-exchanges a webrom token** (`partner-hub.deliveroo.com/api-gw/webrom/logon-pass`),
  so both reads work through the existing headed session (which already passes
  Cloudflare for finance):
  • menu `GET api.webrom.restaurants.deliveroo.com/rom/{rst}/menu`
    → `{categories:[{name, items:[{id,name,description,price,status}]}]}`
  • hours `GET partner-hub.deliveroo.com/api/restaurants/{rst}/opening_hours`
    → `{hours:[{day_of_week, local_start_time, local_end_time}]}`.
  Money: `price` is MAJOR AED units (cake slice 35, 9-piece box 145 — matched to real
  MM prices), no minor-unit scaling. Day origin: `day_of_week` 0=Sunday = MM weekday.
  `parse_deliveroo_menu` / `parse_deliveroo_hours` + unit tests (pass);
  `deliveroo_pull.fetch_deliveroo_menu_hours` (captures both on the Opening-Hours page).
  Read path proven live. **Not deployed** — held during the GrubOps/Cognito incident (a
  deploy restarts the API container). Remaining: push endpoint + snapshot store +
  `_read_deliveroo_*` registration, then one deploy ships it. Create/delete still need
  the write endpoint (a live save capture on the menu editor).
⁷ **Keeta create — VERIFIED end-to-end.** `POST /api/sailorProduct/spu/w/saveSpu`,
  proven through the wired `create_keeta_spu` + `delete_keeta_spu`: create `code 0`,
  item found in the menu read, `deleteSpu code 0`, re-read gone — **no orphan** (clean
  across all 4 shops). Cracked by walking the validation chain: two non-obvious fields
  are required — **`categoryId`** (the platform backend category 后台类目; its value is
  the shop's `listConfig.data.categoryId`, `6669` for MM — the real field is plain
  `categoryId`, not any of the 9 `spu*/backend*/second*` names guessed) and
  **`sourceLanguageType:"en"`** (+ translate-type fields, else `107000632 "original
  language type is empty"`). Two context bugs fixed while wiring: saveSpu/deleteSpu/
  listConfig must run in the page's MAIN world (`page.evaluate`) or they answer
  `107000106 "Restaurant ID required"`; and create/delete prime on the **order-LIST**
  route (sets LOGIN_ACCOUNTID + the restaurant context). `build_keeta_spu_payload`
  (unit-tested) + `create_keeta_spu` (auto-resolves the backend category from listConfig,
  best-effort, or takes it explicitly) + the `create-keeta-item --backend-category-id`
  CLI. Off-shelf (`status=0`) by default; only behind CATALOG_SYNC_ENABLED.

⁸ **Noon create/delete — verified.** The menu is per-item (not a document rewrite):
  `POST /_food-restaurant/menu/item/create` + `/menu/item/delete`. Confirmed by a
  controlled create-then-delete on the MM menu (off-shelf, deleted, re-read gone).
  `noon_provider.create_menu_item`/`delete_menu_item`; `catalog_sync._create_on_noon`.

⁹ **Keeta hours — verified live.** `POST /api/scm/gw/shop/base/summary/list {shopIdList}`
  (the call the order page makes) returns per shop `businessStatus` (1=open) and
  `todayBusinessHours: [{startTime,endTime}]` in **seconds-from-midnight** (28800=08:00,
  84600=23:30 — captured for all 4 shops). This is **today's window only**; Keeta does
  not expose a full weekly schedule on this account, so the read is "open/closed + today",
  not a 7-day schedule. `fetch_keeta_today_hours` (worker) + `parse_keeta_today_hours`
  (API, seconds→HH:MM) + unit test on the real values.

¹⁰ **Keeta delete — endpoint verified + wired.** `POST /api/sailorProduct/spu/w/deleteSpu`
  is the real remove verb: a bad id returns a **validation** error (`shopId not exist!`),
  while `delSpu`/`removeSpu`/`offShelf`/`onOffShelf` return `no matched api config`.
  `delete_keeta_spu` + `delete_keeta_item_in_page` + a `delete-keeta-item` CLI. This is
  the reverse half of the create-then-delete, so a create verification never orphans.

⚙ = writer built + deployed; a successful live create pending the backend-category
field above (create currently validates-and-rejects, leaving nothing behind).

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

**Hours read (✅ verified server-side).** `POST /_food-restaurant/restaurant/outlet/details`
`{outletCode, version:0}` → `data.schedule.periods`: a map of day-index keys →
`[[open,close]]` ranges, keys comma-joined for a shared schedule (e.g. `"0,1,2,3"`).
**The day origin is proven by the response's own `periodsDesc`** — `0=Mon … 6=Sun`
across two outlets — so MM `weekday = (noon_day + 1) % 7`. Times are `HH:MM:SS`.
Verified server-side with the RMS session (2026-09-01). Reader: `_read_noon_hours`
/ `parse_noon_hours` (`noon_provider.get_outlet_details`), registered in
`_HOURS_READERS['noon']`; unit-tested on the real shape. Noon outlets:
`MLTNGM1GBF`/`MLTNGM9FCH`/`MLTNGMTB9M`/`MLTNGMG2B1` (branch map).

**Create + delete (✅ verified by controlled create-then-delete).** Noon's menu is
**per-item**, not a whole-document rewrite (the earlier assumption was wrong).
Confirmed live on the MM menu 2026-09-01 — created an off-shelf `ZZ_PROBE` item,
deleted it, re-read confirmed gone (zero customer impact):
- <span class="path">POST /_food-restaurant/menu/item/create {menuCode, itemType:"main",
  nameEn, nameAr, price, categoryCode, isActive:false}</span> → `{status:"success"}`.
- <span class="path">POST /_food-restaurant/menu/item/delete {menuCode, itemCode}</span>
  → `{status:"success"}` (needs the `n-restaurantcode` header, which `_rms_headers`
  supplies; the create tolerates its absence, delete does not).
- Code: `noon_provider.create_menu_item` / `delete_menu_item`;
  `catalog_sync._create_on_noon` resolves the MM-managed menu + the noon category by
  name and creates off-shelf, recording the mapping from the returned item code.

**How to create.** `POST /api/v1/catalog-sync/items {product_id, target:"noon"}`
(dry-run by default).

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

**Keeta create — writer BUILT (endpoint + payload verified from reads).** The
create/update verb is `POST /api/sailorProduct/spu/w/saveSpu` (an empty POST returns
validation `"Please enter the item name"` — endpoint confirmed non-destructively;
the Edit form saves through the same verb, so it both creates and updates).
`build_keeta_spu_payload` (unit-tested) builds the body; `create_keeta_spu` runs it
in-page (mtgsig), **off-shelf (`status=0`) by default** so a sync never makes an item
live unreviewed. **VERIFIED end-to-end 2026-09-01** through the wired functions:
create `code 0` → item found in the menu read → `delete_keeta_spu code 0` → re-read
gone, no orphan. The two fields that took cracking (found by walking the validation
chain, not the read shape): **`categoryId`** = the shop's `listConfig.data.categoryId`
(`6669` for MM — the platform backend category 后台类目) and **`sourceLanguageType:"en"`**.
Invocable via the CLI:
`aggregator-bootstrap create-keeta-item --shop-id <id> --name "<name>" --category-id <section> --price <n> [--backend-category-id 6669]`
(the backend category auto-resolves from listConfig when the page carries the restaurant
context; pass `--backend-category-id` to be certain). Delete is `delete-keeta-item
--shop-id <id> --spu-id <id>`. Two gotchas baked into the code: these writes run in the
page MAIN world (`page.evaluate`) and prime on the order-LIST route — otherwise
`107000106 "Restaurant ID required"`.

**Keeta delete — endpoint VERIFIED + wired.** `POST /api/sailorProduct/spu/w/deleteSpu`
is the real remove verb (a bad id returns a *validation* error `shopId not exist!`,
while `delSpu`/`removeSpu`/`offShelf`/`onOffShelf` all return `no matched api config`).
`delete_keeta_spu` (keeta_pull) + `delete_keeta_item_in_page` (warm) + a
`delete-keeta-item --shop-id <id> --spu-id <id>` CLI. This is the reverse half of the
create-then-delete, so verifying a create can never orphan an item.

**Keeta hours — VERIFIED (today's window).** `POST /api/scm/gw/shop/base/summary/list
{shopIdList}` (the call the order page makes) returns per shop `businessStatus` (1=open)
and `todayBusinessHours: [{startTime,endTime}]` in **seconds-from-midnight**
(28800=08:00, 84600=23:30 — captured for all 4 shops). This is **today's window only**;
Keeta does not expose a full weekly schedule on this account, so the read is
"open/closed + today", not a 7-day schedule. Shipped: `fetch_keeta_today_hours` (worker)
+ `parse_keeta_today_hours` (API, seconds→HH:MM, closed shop → no shift) + a unit test on
the real captured values. The category `availableTimeDTO` (7 per-day ranges) is
**item-availability, not shop hours** — deliberately not used, as it would misreport drift.

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
| **Talabat create/delete** | Import-based (per-item POST 405s); no portal/write access here | Capture the DH catalog-import from the portal; implement + create-then-delete |
| **Talabat hours read** | On a separate DH availability service | Headed portal capture of the availability endpoint (VM) |
| **Keeta weekly hours** | Today's window is read + verified (§footnote 9); a full 7-day schedule isn't exposed to this portal account | Only if a weekly schedule is needed: find the settings-page schedule endpoint (headed) — else today's window stands |
| **Deliveroo menu + hours** | Decoded, parsers + worker fetch coded & tested; NOT deployed (held for the GrubOps incident) | Add the push endpoint + snapshot store + `_read_deliveroo_*` registration; one deploy ships it |
| **Deliveroo create/delete** | Read decoded; the write (menu-editor save) endpoint not captured | Capture one live save on the `rs-hub.deliveroo.com` menu editor → implement + verify |

**Keeta is now complete** (menu + hours + create + delete, all verified live and
deployed). **Deliveroo menu + hours** turned out reachable after all — decoded, coded,
and tested; only the deploy is pending (held during the GrubOps/Cognito incident, since
a deploy restarts the API container). **Talabat** create/hours are the one genuinely
external-gated area: its product API is read-only (`OPTIONS`→GET-only, `POST`→405) and
its writes/hours live behind the partner-app SPA (`/menu-management-v2`,
`/opening_times_global` — Next.js `.data` + `vagw-api` GraphQL) with no safe off-shelf
test path like Keeta/Deliveroo had.

Everything marked ✅ is verified, shipped, and deployed to production; ⚙ is coded +
tested and awaiting the post-incident deploy.
