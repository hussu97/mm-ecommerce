# Aggregator Portal & Foodics Operations Map

**Purpose:** a discovery map of how each delivery-marketplace partner portal (and
Foodics) lets us do two things per branch — **(A) branch timings & holidays** and
**(B) menu management** (add/remove items & categories; edit name, description,
price, options — *not* just marking out-of-stock). This informs a future admin
feature; nothing here changes live portal state.

**Method:** Claude-in-Chrome against the operator's real logged-in browser,
2026-08-31. Read/observe only — no live timings or menus were modified.

**The governing rule (CONFIRMED, per outlet):** an outlet integrated with
**Foodics** (the Sharjah + Barsha branches) has its **menu pushed from Foodics**;
on every aggregator portal that outlet's menu is then availability-only /
off-limits, and menu edits must be made in **Foodics**. An outlet *not* in Foodics
(Al Karama, Silicon Oasis, any channel-only store) is **edited directly on that
aggregator's own menu tool**. **Hours are always set per-portal** (not pushed from
Foodics), for every outlet. All 5 aggregators + Foodics were walked to confirm
this — details per portal below.

---

## Branch → integration matrix

**Confirmed:** the Foodics account (`862261 - Fatema Cake Sw…`) has **exactly two
branches — Sharjah Kitchen (K001) and Barsha Heights (B001)**. These are the
Foodics/GrubOps-integrated branches (they match reconcile.py's "runs only for
GrubOps branches (Sharjah, Barsha)"). Any aggregator store that is NOT one of
these two is a standalone listing, edited directly on the aggregator portal.

| Branch / outlet | Foodics | Integrated? | Menu edited in | Hours edited in |
|-----------------|---------|-------------|----------------|-----------------|
| Al Majaz / Sharjah (Melting Moments Cakes) — Talabat 711571, Careem 1087801 | **K001** Sharjah Kitchen | **YES** | **Foodics** (synced out) | each aggregator portal |
| Barsha Heights - TECOM — Talabat 728173, Careem 1067984, Deliveroo 693359 | **B001** Barsha Heights | **YES** | **Foodics** (synced out) | each aggregator portal |
| **Al Karama** (Melting Moments, al Karama) — Talabat 715778 | — | **NO** | that aggregator's menu tool directly (e.g. Talabat/Noon non-Foodics menu) | that aggregator portal |
| **Silicon Oasis** — Careem 1069463 | — | **NO** | that aggregator's menu tool directly (Careem **Catalog** editor) | that aggregator portal |

*(outlet ids cross-referenced with `aggregator_branch_map`; Deliveroo outlets
693359/693360/693361 per the deliveroo provider. Fill remaining per-channel ids
for Al Karama / Silicon Oasis from that table.)*

### Capability matrix — what is modifiable, and where

Per integration, the two things a future admin feature would drive. "Menu"
= add/remove items & categories + edit name/description/price/options (not just
OOS). All are **per outlet**.

| Integration | Menu editable? | Menu tool / URL | Opening hours editable? | Hours tool / URL | Holiday / closure control |
|-------------|----------------|------------------|--------------------------|-------------------|----------------------------|
| **Foodics** (master for integrated outlets) | ✅ full CRUD (EN+AR, price, tax, modifiers, combos) | Menu → Menu Builder → Categories / Products / Modifiers | branch open/close **window only** (not the aggregator hours) | Manage → Branches → *Edit Branch* | — (does not drive aggregator closures) |
| **Talabat** | ✅ non-Foodics outlet (full); Foodics outlet = **availability only** | Menu `…/menu-management-v2?activeMenuId=` | ✅ per-day, multi-shift | Opening Times `…/opening_times_global` | **Branch Status** pause (no calendar) |
| **Deliveroo** | ✅ full, but behind a **separate "Menus" login**; hub = bundles/availability | Menu Manager → *Edit menu* → Menus (`/login?return_to=/menus/…`) | ✅ per-day, multi-shift | Opening hours `/opening-hours?...&branchId=` | **Days off** (full days) + one-off **events** |
| **Noon** | ✅ **non-Foodics** Menu Maker (+ **QC submit**); Foodics Menu Maker off-limits | Menu Maker `…/menu-maker/<menuId>/…` | ✅ per outlet (badged *New*) | Opening Hours (per outlet) | to confirm |
| **Careem** | ✅ non-Foodics outlet (**Catalog**); Foodics outlet Foodics-fed | Catalog `/merchant/catalog/<outletId>/<catalogId>` | ✅ per-day, multi-slot, Copy-to-all-days | Operational hours `/merchant/operational-hours/<outletId>` | per-day toggle / **Outlet Closed** availability |
| **Keeta** | ✅ full CRUD | Menu `/m/web/product` | ✅ per-day, up to **5 periods/day** | `/m/web/shop#/settings` → Opening hours | **Special hours** tab + Restaurant status |

**Read this as:** menu writes route to **Foodics** for the two integrated outlets
and to the **per-channel tool** for every other outlet; **hours + holidays** are
written to **every channel directly**, for **every** outlet (Foodics never carries
them).

---

## 1. Talabat — `partner-app.talabat.com`  ✅ explored

Scope: a **store switcher** (top-left sidebar) scopes everything to one store;
3 stores under brand "Melting Moments (666733)". Nav groups: *Monitor* (Dashboard,
Order History, Performance, Reviews, Reports), *Manage your business* (Payments,
**Menu**, **Opening Times**, Settings), *Grow* (Advertising, Promotions).

### (A) Timings & holidays — **editable**
- **Menu → Opening Times** (`/opening_times_global`), per store. A **Regular
  schedule** table (Mon–Sun, from–to). **Edit** opens a per-day editor: enable
  toggle per day, from/to time, **Add shift** (multiple shifts/day for split
  hours), delete shift; a multi-step "Next" flow (likely apply-to-stores / special
  hours step).
- **Holidays / special closures:** no dedicated holiday calendar in the regular
  schedule. Temporary closures are done via **Branch Status** (pause/close for a
  period) — help article "Managing Your Branch Status on talabat". So "holiday for
  a branch" = a Branch Status pause, not a recurring-hours exception. *(Confirm
  whether the "Next" step in the schedule editor exposes special/holiday dates.)*

### (B) Menu — **editability is per-outlet, gated by Foodics integration**
- **Menu** (`/menu-management-v2?activeMenuId=<menuId>`), per store. Organised by
  **categories** (Brownies, Cookies, Cookie Melt, Mix Boxes, Cakes, Desserts,
  Eggless, Extras, …) each with a product count; per product: image, name,
  description, price, availability toggle, **View options** / **Show sizes**
  (variants & modifiers); an **Options** tab; **Bulk Availability**; a **sort**
  action (categories/products/option groups).
- **On a Foodics-integrated outlet** (e.g. *Melting Moments Cakes* — Al Majaz /
  Sharjah): a banner *"Menu editing is restricted — your role only allows changing
  item availability… contact your manager"*, prices shown as AED 0.00. Menu is
  **push-only from Foodics** — availability toggling only.
- **On a NON-Foodics outlet** (e.g. *Melting Moments, al Karama* — 34 41st St, Al
  Karama, Dubai; `activeMenuId=1334277`): **full editing** — **Add category**, edit
  category, edit product name/description/**price**, **Show sizes** (size/price
  variants), **Options**, **Adjust prices**, sort. No restriction banner.
- ⇒ The restriction is **per-outlet by integration status**, not a role limit:
  edit an integrated outlet's menu in **Foodics**; edit a non-integrated (Karama)
  outlet's menu here on Talabat.

---

## 6. Foodics — `console.foodics.com`  ✅ explored (the master for integrated branches)

Account `862261 - Fatema Cake Sw…`. Nav: Dashboard, Orders, Customers, Reports,
Inventory, **Menu**, **Manage**, Marketing, Marketplace.

### (B) Menu — **full CRUD, this is where integrated branches are managed**
- **Menu → Menu Builder → Categories / Products / Modifiers / Combos / Groups.**
- **Products** (`/menu/products`): **Create Product**, **Import/Export** (bulk CSV),
  Filter, All/Active/Inactive/**Deleted** tabs; list shows image, Name, SKU
  (FG0130…), Category, **Price**, Tax Group, Active. The product/category set
  matches what shows on Talabat (Cakes, Cookie Melt, Desserts, Ramadan…) — i.e.
  **Foodics is the source that syncs to the aggregators** (via GrubTech/GrubOps).
- **Product detail / Edit Product**: **Name + Name Localized (Arabic)**, Category,
  SKU, Barcode, Tax Group, **Pricing Method / Price**, Selling Method, Costing
  Method, Ingredients Cost, Calories, Prep Time, etc. **Deactivate Product** (soft)
  and a Deleted tab (remove). ⇒ add/remove/rename/reprice/translate and manage
  **Modifiers** (options) all happen here. This is the surface a future admin
  feature should drive (Foodics API) for the two integrated branches.

### (A) Timings — a single branch window (NOT the aggregator delivery schedule)
- **Manage → Branches** lists the 2 branches. A branch (e.g. Sharjah Kitchen K001)
  has **Opening From (08:00) / Opening To (23:00)** — one operating window, plus
  Inventory End of Day, Tags, **Delivery Zones**, **Edit Branch** / **Copy
  Settings**. This is a single daily window, *not* a per-day-of-week schedule and
  *not* a holiday calendar.
- **Important:** this Foodics window did **not** match Talabat's per-day delivery
  schedule (which was independently editable on Talabat). So even for integrated
  branches, **the aggregator delivery hours are set on each aggregator portal, not
  pushed from Foodics.** The Foodics↔aggregator integration is primarily the
  **menu**; hours/holidays are per-portal. *(Confirm whether GrubTech pushes hours
  at all, or only the menu.)*

---

## 2. Noon — `restaurant.noon.partners`  ✅ explored (nav confirmed)

Logged in (noon Food Partner, project `PRJ135208`). A **Delivery / noon OUT**
toggle; **Filter by outlets** (per-outlet scope). Nav: Performance, AI Manager,
Order History, **Outlets**, **Opening Hours** (badged *New*), **Menu Maker**,
Ratings and Reviews; Marketing (Ads, Discounts, Rewards); Payments, Documents;
Access Control, Your Profile; Settings.

- **(A) Timings & holidays:** **Opening Hours** (badged *New*), per outlet.
  *(Confirm its holiday/special-day control.)*
- **(B) Menu — TWO menu-makers, split by integration:**
  - **Foodics-related outlets menu** (`…/menu-maker/M2844594958216136347013554A/…`)
    — **do NOT edit here**; it is fed from Foodics.
  - **Non-Foodics outlets menu** (`…/menu-maker/M3497661938091593085106254A/…`)
    — **full CRUD**: **Add category**, **Add new item**, per-item edit
    (name/price/description), drag-reorder, **Customization groups** (modifiers),
    **History of Changes**; status shows **Published**. **Changes must be submitted
    for QC** before they go live.
  - Both under restaurant `R5967280642376629909871448A`, project `PRJ135208`.
- **Outlets** lists the branches; noon has more outlets than the 2 Foodics
  branches, so the non-Foodics menu-maker is where those are edited.

## 3. Careem — `partners.careem.com/saturn-ext/…`  ✅ explored

Logged in (Partner, "Outlet Owner", "Open: 2/3"). Nav: Overview (Dashboard,
Analytics and reports, Recent orders), Grow (Marketing, Ads), Operations (Refunds
and Complaints, **Outlets Management**), Your Business (Finances, User management).
**Outlets Management** lists 3 outlets, each with **Edit Ops Hours** + **Edit Menu**
+ an availability toggle and *Outlet Closed/Active* status:

| Outlet | id | Zone / City | State | In Foodics? |
|--------|----|-------------|-------|-------------|
| Melting Moments | 1087801 | Al Majaz, **Sharjah** | Outlet Closed | yes (Sharjah Kitchen) |
| Melting Moments | 1069463 | Silicon Oasis, Dubai | Active | **no** (Careem-only) |
| Melting Moments | 1067984 | Barsha Heights, Dubai | Active | yes (Barsha) |

### (A) Operational hours — **editable** (`/merchant/operational-hours/<outletId>`)
- Per **outlet**, per day (Sun–Sat): enable toggle, from/to time, **Add slot**
  (multiple slots/day), **Copy to all days**, **Save**. Confirmed on the Barsha
  outlet (e.g. Sun 5:00 PM–10:00 PM, weekdays 8:00 AM–9:45 PM, Wed disabled).
- No holiday calendar. "Holiday" = disable a day, or flip the outlet's
  **availability** to *Outlet Closed* in Outlets Management (the Sharjah outlet was
  in that state — its hours page 403'd "Access Forbidden" while closed).

### (B) Menu — **editable for non-Foodics outlets** (`/merchant/catalog/<outletId>/<catalogId>`)
- A full **Catalog** editor per outlet: **Menu Items** / **Unavailable Items** tabs,
  **Add New Item**, **Categories** with **+** add / edit / reorder (Brownies,
  Cookies, Cookie Melt, Boxes, Desserts, Cakes, Extras), per-item price +
  availability toggle + duplicate + drag-reorder, category-level availability.
  Confirmed on the **Silicon Oasis** (non-Foodics) outlet
  (`/catalog/1069463/1046472972`).
- Same per-outlet rule as the others: this catalog is the place to edit a
  **non-Foodics** Careem outlet's menu. The Foodics-integrated outlets (Sharjah,
  Barsha) are fed from Foodics — edit those in Foodics, not here.
  *(The "Edit Menu" button in Outlet Management opens this catalog; earlier it
  appeared inert because it was tried on an integrated/closed outlet.)*
## 4. Deliveroo — `partner-hub.deliveroo.com`  ✅ explored

Logged in as account **FATEMA CAKE SWEETS** (same legal entity as the Foodics
account), store "Melting Moments Cakes-Barsha" (`orgId=497912&branchId=693359`).
Nav: Home, Ratings, Sales, Live orders, Operations, **Menu Manager**, Team,
Benefits, Invoices, Reports, Marketer, **Opening hours**, Settings,
**Integrations**, Help. Per-branch store switcher (top-right).

### (A) Timings & holidays — **editable, richest holiday support seen**
- **Opening hours** (`/opening-hours?...&branchId=`), per branch. Per-day schedule
  (Mon–Sun) with rider-availability windows and **Add hours** (multiple shifts),
  remove/delete, **Edit availability**.
- **Days off** — a dedicated control: *"If your restaurant is closed for a full day
  or longer, you can manage this in Days off"* → the real **holiday** mechanism.
- **One-off changes / "View all events"** — alter hours for specific days
  (special/holiday hours) without touching the regular schedule.
- All in the Partner Hub directly (no separate login).

### (B) Menu — editable, but behind a SEPARATE "Menus" login
- **Menu Manager** (`/menus?...`) in the hub: shows the menu, **Create bundle**,
  and "quick updates to items, categories, options and bundles"; per-menu **Edit
  menu** / more actions. No role-restriction banner (unlike Talabat).
- **Edit menu** links out to Deliveroo's separate **"Menus"** editor
  (`/login?return_to=/menus/…`) which prompts its **own login** (email + password).
  Full item/category/option/bundle editing lives there. *(Not entered — I don't
  authenticate on the operator's behalf. To document its internals, the operator
  logs into that Menus tool and I can then walk it.)*
- **Integrations** section in the hub is worth checking to confirm the Foodics/
  GrubTech link and whether it makes the menu push-only for this branch.
## 5. Keeta — `merchant.mykeeta.com`  ✅ explored (most self-service — both editable)

Logged in (Keeta Manager Portal, store "Melting Moments — Open"). A **Group view**
toggle + per-branch store switcher (top-left). Nav: Performance, Orders,
Financials, Reviews, Reports; **Menu**, **Restaurant status**, **Opening hours**,
**Settings**; Promotion centre, My promotions. All branch-specific.

### (A) Timings & holidays — **editable, with a holiday tab** (`/m/web/shop#/settings`)
- **Opening hours** tab: per-day (Sun–Sat) weekly schedule, **up to 5 time periods
  per day**, **Set opening hours**.
- **Special hours** tab: dedicated **holiday / special-day** hours.
- **Restaurant status** (separate nav item): pause/close temporarily.

### (B) Menu — **full CRUD** (`/m/web/product`)
- A full menu builder: **category** list (left, with per-category product counts),
  add-category and add-product (**+**) actions, **import/export** (upload/download),
  edit, copy, reorder; per-item settings; **Preview**. Add/remove items &
  categories and edit name/price/options are all available here, per branch.

Keeta is the one channel where the operator does BOTH hours and menu directly in
the portal (confirmed by the operator).

---

## Conclusions

### The architecture
- **Foodics is the menu master for the two integrated branches** (Sharjah Kitchen,
  Barsha Heights). Its product/category set is what appears on the aggregators, so
  the menu is authored in Foodics and pushed out (GrubTech/GrubOps). Foodics gives
  full CRUD: create/edit/deactivate/delete **products** (name + Arabic localized,
  price, category, tax, SKU, modifiers), **categories**, **modifiers**, combos,
  groups; bulk import/export.
- **THE menu rule — split by Foodics integration, per outlet, NOT per channel:**
  - A **Foodics-integrated outlet** (the Sharjah + Barsha branches) has its menu
    **pushed from Foodics**. On the aggregator portal that outlet's menu is
    **locked to availability toggling** (Talabat shows the "editing restricted"
    banner; Noon keeps a separate "Foodics outlets" menu you must not touch). Edit
    these menus **only in Foodics**.
  - A **non-Foodics outlet** (e.g. Talabat/Noon **Al Karama**, Careem **Silicon
    Oasis**, any channel-only store) is **edited directly on that aggregator's own
    menu tool** — there is no Foodics to sync it.
- **Where the non-integrated menu edit happens, per channel:**
  - **Talabat** — full editor on the non-Foodics outlet's Menu (Add category, price,
    sizes/options, sort). Integrated outlet = availability-only.
  - **Noon** — the **non-Foodics Menu Maker** (full CRUD + Customization groups),
    then **submit for QC**. The Foodics Menu Maker is off-limits.
  - **Keeta** — **full menu CRUD** in-portal for its outlets (categories, products,
    prices, options, import/export). No separate login.
  - **Deliveroo** — full item/category/option/bundle editing via a **separate
    "Menus" login**; the hub's Menu Manager itself is bundles/availability.
  - **Careem** — a full **Catalog** editor per non-Foodics outlet
    (`/merchant/catalog/<outletId>/<catalogId>`): Add New Item, categories,
    prices, availability, reorder. (Integrated outlets are Foodics-fed.)
- **Hours are per-portal, not pushed from Foodics** — even for integrated
  branches. Foodics only holds a single branch open/close window; the actual
  aggregator delivery hours were independently set (and differed) on Talabat.

### Timings & holidays, per channel
| Channel | Regular hours | Holiday / special-day |
|---------|---------------|------------------------|
| Talabat | per-day schedule, multi-shift, editable | **Branch Status** pause (no holiday calendar) |
| Deliveroo | per-day schedule, multi-shift, editable | **Days off** (full-day closures) + one-off **events** |
| Noon | **Opening Hours** per outlet | to confirm (has "New" Opening Hours) |
| Careem | per-day, multi-slot, Copy-to-all-days, editable | disable a day / **Outlet Closed** availability (no holiday calendar) |
| Keeta | per-day, **up to 5 periods/day**, editable | **Special hours** tab + **Restaurant status** pause |
| Foodics | single open/close window per branch | none (not the aggregator hours) |

### Recommended approach for a future admin feature
1. **Menu — route by integration status.** For the two Foodics branches, drive the
   **Foodics API** (one source, syncs to all channels). For any non-integrated
   store, use that channel's menu tool/API. Never dual-edit an integrated branch's
   menu on the aggregator portal.
2. **Hours & holidays — fan out per channel per branch.** There is no central push,
   so a feature must talk to each portal's own schedule + holiday/pause mechanism:
   Deliveroo (Days off / events), Talabat (schedule + Branch Status), Noon (Opening
   Hours), etc. Prefer each partner's **API** where one exists; fall back to headed
   portal automation (the same real-Chrome-under-Xvfb approach the ingest already
   uses) where the capability is UI-only (e.g. Deliveroo's separate Menus tool).
3. **Confirm the API surface** of Foodics (well-documented public API — menu,
   branches, business hours) and each aggregator's partner API for hours/menu
   before committing to API-vs-automation per capability.

## Open items (minor, for a deeper pass)
- **Deliveroo "Menus"** editor internals: only walked to its separate login; log
  in there to document its exact item/category/option/bundle screens.
- **Noon / Talabat Opening Hours** editors: confirm each one's holiday/special-day
  control (Keeta = Special hours; Deliveroo = Days off/events; Careem = per-day
  toggle / Outlet Closed; Talabat = Branch Status — Noon's TBC).
- Confirmed: GrubTech/Foodics pushes only the **menu** for integrated outlets, not
  the **hours** (hours are per-portal for every outlet).

---

# Plan — a central "catalog & hours sync" feature

Goal: manage the catalog and the branch hours/holidays **once, in MM**, and push
them to the right target for each outlet — **menu → Foodics** for Foodics-managed
outlets and **menu → the aggregator's own tool** for non-Foodics outlets; **hours
+ holidays → every aggregator directly**, for every branch (Foodics never carries
them). The operator picks, per catalog item/category, whether it syncs out, and
sees a **diff against every integrator** before anything is written.

## 1. MM is the source of truth

Two source-of-truth domains, both already partly in MM:

- **Catalog** — categories, items (name EN/AR, description, price, image), and
  **item modifiers** (option groups + options, e.g. sizes/add-ons). MM already has
  a storefront catalog; extend it with the sync metadata below.
- **Branch schedule** — each branch's weekly open/close times **and a holiday /
  closure calendar** (one-off closed days / special hours). New in MM.

Everything downstream is a projection of these two, per outlet.

## 2. Per-item control — the "sync to aggregators" switch

The operator must choose *which* catalog rows leave MM (some products are
storefront-only or POS-only). So add sync metadata on **category** and **item**
(and inherit onto modifiers):

- `sync_to_aggregators: bool` — master on/off for this row.
- `sync_channels: set` — optionally restrict to specific channels (default: all
  the outlet's live channels). Lets an item go to Keeta but not Deliveroo, etc.
- `sync_status` per (row × channel × outlet): `in_sync | drift | pending | error`,
  plus `last_synced_at` and the last diff.

In the admin catalog, each category/item gets a **"Sync into food aggregators"**
toggle and a per-channel expander showing its live state on each integrator.

**Identity maps** make push + diff possible (this is the missing plumbing):
`mm_category ↔ channel_category_id`, `mm_item ↔ channel_item_id`,
`mm_modifier_group/option ↔ channel ids`, keyed per **outlet**. Seed them from a
first full read (below); thereafter maintain on create. `aggregator_branch_map`
already does this at the outlet level; add item/category/modifier-grained maps
(GrubOps already has a partial `grubops_item_map` to model on).

## 3. Read side first — full menu ingest + a cross-integrator diff

Before writing anything, MM must **see** each integrator's current menu (today the
scrapers pull orders/finance, not the full menu). Extend each channel client with
a `fetch_menu(outlet)` that returns categories/items/modifiers/prices, then:

- **Diff engine:** for each outlet, compare MM's sync-flagged catalog against every
  integrator's live menu and against **Foodics** (for integrated outlets). Emit a
  per-outlet, per-channel delta: *missing / extra / price-mismatch / name or
  description drift / modifier difference*. This is the "compare and check with all
  the integrators what is the difference" the operator asked for.
- Surface the diff in admin (a per-item badge + a per-outlet drift report). Nothing
  is written yet — this read-only phase is safe and immediately useful (it catches
  the price/menu drift that already bit us once).

Where a channel's menu is **not** machine-readable, the diff falls back to the
headed-Chrome read the ingest already uses.

## 4. Menu write — route by outlet, gated by the flag + the diff

Only sync-flagged rows, only after the operator approves the diff:

- **Foodics-managed outlets (Sharjah, Barsha):** write to the **Foodics API**
  (products/categories/modifiers/prices/localized names); Foodics/GrubTech
  propagates to the aggregators. **Never** write these outlets' menus on an
  aggregator portal — that fights the sync.
- **Non-Foodics outlets (Al Karama, Silicon Oasis, channel-only):** write to that
  channel's own menu surface:
  - **API where one exists** — confirm each partner's menu-write API (Deliveroo,
    Talabat, Careem, Noon, Keeta). Prefer it.
  - **Headed automation where the menu is UI-only** — reuse the worker's
    real-Chrome-under-Xvfb sessions to replay the operator's edits: Careem
    **Catalog**, Talabat **Menu**, Keeta **Menu**, Noon **Menu Maker**, Deliveroo
    **Menus** (separate login).
  - **Channel quirks to encode:** Noon requires **submit for QC** after edits;
    Deliveroo menu is behind the **separate Menus login**; Careem catalog is
    per-outlet (`/catalog/<outletId>/<catalogId>`); Talabat's schedule/menu applies
    via a multi-step flow.

Each write is idempotent per (row × outlet × channel) and records `sync_status`;
a failed push flags `error` and never blocks the others (same isolation the ingest
sweep uses).

## 5. Hours & holidays write — fan out to ALL channels, ALL branches

Foodics does not carry aggregator hours, so MM pushes the branch schedule to every
channel directly:

- **Regular hours:** map MM's weekly schedule onto each channel's editor — Talabat
  Opening Times, Deliveroo Opening hours, Noon Opening Hours, Careem Operational
  hours, Keeta Opening hours. Normalise MM's model (per-day, multiple shifts) to
  each channel's shape (Keeta caps at 5 periods/day; Careem/Talabat/Deliveroo take
  multiple slots).
- **Holidays / closures:** MM holds one calendar; each channel expresses it
  differently, so translate per channel:
  - Deliveroo → **Days off** (full days) / **events** (special-day hours)
  - Keeta → **Special hours**
  - Talabat → **Branch Status** pause for the window
  - Careem → disable the day(s) / flip **Outlet Closed**
  - Noon → its Opening Hours special control (confirm)
- Prefer each channel's **hours/holiday API** where present; fall back to headed
  automation. This is lower-risk than menu writes (bounded, reversible) and high
  value (nothing does it centrally today) — a good first writer to ship.

## 6. Suggested phasing

1. **Model + read + diff (safe):** catalog sync metadata + identity maps; branch
   schedule/holiday model; `fetch_menu` per channel; the cross-integrator diff +
   admin drift report. No writes.
2. **Hours & holidays writer:** fan-out to all channels for all branches (API where
   available, else automation). Bounded and reversible.
3. **Menu writer:** Foodics API for integrated outlets; per-channel API/automation
   for non-Foodics outlets; gated by the per-item **sync** flag and the
   operator-approved diff; encode the QC/publish/separate-login quirks.

## 7. Open questions to close before building

- **Partner write APIs:** which of Foodics / Deliveroo / Talabat / Careem / Noon /
  Keeta expose **menu-write** and **hours/holiday-write** APIs (vs UI-only)? This
  decides API-vs-automation per channel and is the biggest unknown.
- **Foodics → aggregator propagation:** how fast, and does editing an integrated
  outlet's item on Foodics reliably reach every channel (and overwrite a manual
  aggregator edit)?
- **Modifier mapping fidelity:** do all channels model option groups/options the
  same way MM/Foodics do (min/max, required, per-option price)? The diff will
  surface mismatches; some may be unmappable.
- **Noon QC latency** and **Deliveroo Menus** automation: both add steps a naive
  API push would skip.
