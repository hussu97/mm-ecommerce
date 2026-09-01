# Catalog & Hours Sync — Pre-Build Audit

**Question this answers:** *if we made MM the source of truth and pushed its
catalog + branch hours out to the delivery marketplaces (and Foodics), what would
actually change on each outlet, and what would break?* This is the read-only
"compare against every integrator" pass the operator asked for **before** any code
is written or any portal is touched.

**Method:** read-only, 2026-08-31. MM side pulled from the production storefront API
(`api.meltingmomentscakes.com`, public catalog endpoints). Portal side read live
through the operator's own logged-in browser (Claude-in-Chrome), the same sessions
the operations-map doc was built from. **Nothing was written to any portal, Foodics,
or MM.** No feature code exists yet — this audit is the gate in front of it.

**Companion:** [`aggregator-portal-operations-map.md`](aggregator-portal-operations-map.md)
established *where* each portal lets you edit menu/hours and the Foodics-vs-direct
rule. This audit adds the *item-, price- and hour-level drift* that map could not.

---

## 0. TL;DR — the findings that shape the build

> **Updated 2026-08-31 after operator input:** (a) **deleting** aggregator items not
> in the MM menu **is allowed** — the sync is authoritative, not merely additive;
> (b) the real Foodics→aggregator mechanism is a **"Grubtech" menu group**
> (membership) + a **"Grubtech" price tag** (the aggregator price), both read live
> for this audit. See §2c. The findings reflect that.

1. **For the two integrated branches (Sharjah, Barsha) the aggregator menu is
   governed by two Foodics objects — not by editing each portal, and not by the
   whole 131-item catalogue.** [Groups → **Grubtech**](https://console.foodics.com/menu/groups/a062ba1a-70b6-4bd7-8dac-f7986f33727f)
   is a group whose **9 subgroups** (Cookie Melt, Cakes, Extras, Cookies, Mix Boxes,
   Brownies, Eggless, Desserts, **New In**) are exactly what GrubTech pushes to the
   marketplaces — which is why the integrated outlets show ~46 items, not 131.
   [Price Tags → **Grubtech**](https://console.foodics.com/menu/price-tags/a056ee7e-5823-47af-9ab5-1029508c996b)
   carries the **aggregator price**, a *separate* number from the product's Foodics
   ("Original") price. **⇒ The MM→Foodics writer manages the Grubtech group
   membership + price tag, and never touches the 85 café/POS items.** The "mirror
   would delete 86 products" risk from the first draft is moot — the writer targets
   the group/price-tag, not the catalogue.

2. **Operator policy: the price-tag Price MUST equal the product's Original price.**
   Managing the aggregator set through the price tag means every price change updates
   **both** the product **and** the price-tag entry, in lockstep. Live read shows
   this holds today for everything **except three items** — Ramadan Advent Box 12 pc
   (**55 → 70**), 30 pc (**135 → 155**), Christmas Advent Calendar (**55 → 70**) —
   which carry an aggregator uplift. Under the parity policy these are the only
   violations. *Decision to confirm: enforce strict parity everywhere, or keep those
   seasonal uplifts as declared exceptions?*

3. **`sales_channels` is the wrong switch to seed "goes to aggregators"; the
   Grubtech group is the right one.** 16 of MM's 45 products — **every Brownie (9)
   and Cookie (7)** — are flagged `sales_channels = ['web']`, yet they are live on
   every marketplace (they sit in the Grubtech group). Seed the MM
   `sync_to_aggregators` flag from **current Grubtech group membership (~46 items)**,
   never from `sales_channels`.

4. **Names, categories and availability have already drifted between channels.**
   Careem calls the box category **"Boxes"** where MM/Talabat call it **"Mix
   Boxes"**; Careem writes **"Dark Chocolate & Walnut Brownies"** where MM writes
   **"…and Walnut…"**; Keeta has moved **"Chocolate & Whipped Salted Caramel Cake
   Slice"** out of *Cakes* into *New In* and marked it **Unavailable**. A diff
   engine must treat name/category/availability as first-class deltas, not noise.

5. **Branch hours are the highest-value, lowest-risk first writer — because they
   are a mess today.** The *same physical kitchen* (Barsha Heights) advertises
   **four different weekly schedules** across four surfaces, including **Careem
   being closed every Wednesday** while every other channel is open. Almost none of
   this looks intentional. MM has **no per-day schedule model at all** yet (only a
   single open/close window), so it cannot even represent — let alone reconcile —
   what the portals hold. That gap has to be filled before hours can sync.

---

## 1. The MM side (candidate source of truth)

Pulled live from `GET /api/v1/products?per_page=500` + `/categories` on production.

| Category | MM products | Pricing shape |
|----------|-------------|----------------|
| Brownies | 9 | `base_price = 0`; real price in a **"Your Choice of Quantity"** modifier — 3 / 6 / 9 Pieces (e.g. Fudge = 50 / 90 / 125). **`sales_channels=['web']`** |
| Cookies | 7 | same 3/6/9 variant shape (e.g. Nutella = 45 / 90 / 135). **`sales_channels=['web']`** |
| Cookie Melt | 8 | flat `base_price` (250 g = 40, 500 g = 70). pos+web |
| Mix Boxes | 9 | flat (45–145) **plus** a composite "pick-N" modifier (choose 9 cookies). pos+web |
| Cakes | 7 | flat (30–55). pos+web |
| Desserts | 3 | flat (30). pos+web |
| Eggless | 1 | Eggless Fudge Brownies. pos+web |
| Extras | 1 | Gift Note Card (AED 5). pos+web |
| **Total** | **45** | 16 of 45 web-only (all Brownies + Cookies) |

**Two structural facts the sync must encode from the MM side alone:**
- **Variant pricing lives in a modifier, not `base_price`.** For 16 products the
  base price is literally `0`; a push that read `base_price` would ship free
  brownies. The mapping has to treat the "Your Choice of Quantity" modifier as the
  price carrier (the marketplaces model this as "sizes").
- **`sales_channels` ≠ "sells on aggregators."** See finding #3.

MM's Arabic names live in a `translations` JSONB, not a dedicated column — the
push/diff must read localized names from there (Foodics has a first-class
`Name Localized`, so this is a mapping point, not a blocker).

---

## 2. The portal side — what each outlet actually carries

### 2a. Foodics — the master for the two integrated branches
`console.foodics.com`, account `862261 – Fatema Cake Sw…`, **131 products across 19
categories**:

| | Categories |
|---|---|
| **Shared with MM (8)** | Brownies **18**, Cookies **14**, Cakes **8**, Cookie Melt **8**, Mix Boxes **9**, Desserts **3**, Eggless **2**, Extras **1** |
| **Foodics-only (11)** | Hot Coffee 14, Drinks 11, Juices 10, Cold Coffee 7, Savouries 6, Tea 5, Smoothies 4, Snacks 4, Momos 4, **Ramadan 2**, **Christmas 1** |

Even inside the *shared* categories Foodics is bigger (Brownies 18 vs MM 9, Cookies
14 vs MM 7) — extra flavours/POS variants MM never had. Many café items are priced
`0`; "Cake – Customer Specification" is **Open Price**; "Christmas Advent Calendar"
is **Inactive**. **⇒ Foodics is a superset of MM in every dimension. MM is the
delivery subset of a café POS catalogue.**

### 2b. The aggregator menus (what customers actually see)

| Outlet | Channel | Foodics? | Categories seen | Editability | Notes |
|--------|---------|----------|-----------------|-------------|-------|
| **Al Karama** (793319) | Talabat | No | 8 MM cats **+ Ramadan (2)** | **Full edit** ("Show sizes", Add category, Adjust prices) | Names/descriptions match MM verbatim. Talabat nudge: *"match your dine-in prices"* → its prices differ from Foodics. |
| **Barsha** (728173) | Talabat | Yes | 8 MM cats **+ New In (1)** | **Availability only** — *"Menu editing is restricted… contact your manager"* | Prices render `AED 0.00` to this role; real price behind "View options". |
| **Silicon Oasis** (1069463) | Careem | No | Brownies, Cookies, Cookie Melt, **Boxes**, Desserts, Cakes, Extras (**7; no Eggless, no Ramadan**) | **Full Catalog edit** | Category **"Boxes"** ≠ "Mix Boxes"; **"Dark Chocolate & Walnut"** ≠ MM's "…and Walnut"; **Fudge Brownies unavailable**. |
| **Barsha** (…189187) | Keeta | Yes | 8 MM cats **+ New In (1)** | **Full CRUD** (Keeta is self-service) | Real prices shown (AED 35). **"Chocolate & Whipped Salted Caramel Cake Slice" moved to *New In* + marked Unavailable**; Cakes = **6** here vs 7 (MM) vs 8 (Foodics). "Copy to other restaurants" available. |

Outlets confirmed live: **Careem** — Sharjah 1087801 *Outlet Closed* (access
forbidden), Silicon Oasis 1069463 *Active*, Barsha 1067984 *Active*. **Keeta** — 4
outlets: Karama, Sharjah, DSO, Barsha. **Talabat** — 3 stores: Sharjah 711571 &
Barsha 728173 (brand 666733), Al Karama 793319 (brand 715778). All match
`aggregator_branch_map`.

> **Not item-level re-read in this pass:** Noon and Deliveroo. Their *structure* is
> in the operations map (Noon = two menu-makers split by Foodics + a QC-submit step;
> Deliveroo Barsha = Foodics-fed, menu edits behind a **separate Menus login**). The
> conclusions below don't depend on their item lists; the read-side automation
> (Phase 1 of the build) will enumerate them exhaustively. Flagged honestly so the
> audit doesn't imply coverage it doesn't have.

---

### 2c. The Foodics "Grubtech" mechanism — the real integrated-branch sync path

For Sharjah + Barsha, GrubTech does **not** push the whole Foodics catalogue. Two
Foodics objects govern the aggregator menu, both read live for this audit:

**(i) The "Grubtech" menu group — *which* products appear.**
[`/menu/groups/a062ba1a…`](https://console.foodics.com/menu/groups/a062ba1a-70b6-4bd7-8dac-f7986f33727f).
Its own Products list is empty; it works entirely through **9 subgroups**, one per
delivery category: **Cookie Melt, Cakes, Extras, Cookies, Mix Boxes, Brownies,
Eggless, Desserts, New In**. Each subgroup holds the *curated delivery subset* of
that category — e.g. the Brownies subgroup carries 9 of Foodics' 18 brownies. The
café/POS categories (Coffee, Tea, Juices, Momos, Savouries, Snacks, Drinks) are **not
in this group**, which is exactly why the marketplaces show ~46 items, not 131.
**The operator's stated build model:** create one group per category, add the
delivery products to it, and nest those groups under `Grubtech`. Membership of
`Grubtech` == the aggregator product set. Removing a product from its subgroup is how
you **delete** it from the aggregators (operator confirmed deletion is fine).

**(ii) The "Grubtech" price tag — *at what price*.**
[`/menu/price-tags/a056ee7e…`](https://console.foodics.com/menu/price-tags/a056ee7e-5823-47af-9ab5-1029508c996b),
under Menu Settings → Price Tags. Every row shows **Original Price** (the product's
Foodics price) beside **Price** (what the aggregator charges) — a genuinely separate
number. Tabs: **Products / Combos / Modifier Options**. Live read:

| Where | Rows | Original vs Price |
|-------|------|-------------------|
| Products (~46) | cakes/cookie-melt/mix-boxes/desserts | **parity** (35/35, 40/40, 70/70, 55/55, 90/90, 135/135, 145/145…) |
| Products | **Ramadan 12 pc** | 55 → **70** (uplift) |
| Products | **Ramadan 30 pc** | 135 → **155** (uplift) |
| Products | **Christmas Advent** | 55 → **70** (uplift) |
| Modifier Options | **3 / 6 / 9 Pieces** (brownie/cookie sizes) | 50/50, 90/90, **125/125** — parity |
| Modifier Options | Mix-box variants | 55/55, 145/145 — parity |
| Modifier Options | per-flavour pick-N options | 0/0 (no upcharge) |

So the aggregator price for the variant-priced brownies/cookies lives in the price
tag's **Modifier Options**, not on the product. **Operator policy: keep price-tag
Price == product Original Price**, updating both together on any price change — the
three seasonal uplifts are the only current exceptions.

**What this means for the writer (integrated branches):**
- "Add to aggregators" = add the product to its category subgroup under `Grubtech`
  **and** add it to the `Grubtech` price tag at the product's price.
- "Remove from aggregators" = drop it from the subgroup (and the price tag).
- "Reprice" = set the product price **and** the price-tag entry (Product row or
  Modifier Option row) to the same number.
- The writer touches **only** the `Grubtech` group + price tag — never the 85
  café/POS products, never the Foodics product catalogue at large.
- Non-Foodics outlets (Al Karama, Silicon Oasis) have no Grubtech object — those are
  edited on the portal directly (deletion allowed there too).

## 3. Menu diff — what the sync *would* do, per outlet

Read this as the output the diff engine will produce, hand-computed from §1–§2.

### Non-Foodics outlets (MM would write here directly)
- **Talabat Al Karama** ≈ **MM + Ramadan (2)**. A push would be **near-clean**:
  same 8 categories, same product counts, matching names/descriptions. The only
  deltas: (a) **Ramadan (2 items)** exists on Talabat but not MM → deletion is now
  allowed, so the sync **would remove it** unless MM carries Ramadan or it is marked
  keep-on-channel; seasonal ranges are the case to handle deliberately here;
  (b) **price parity** needs checking item-by-item (Talabat itself flags a dine-in
  price mismatch); (c) variant/"sizes" mapping for the 3/6/9 brownies must line up.
- **Careem Silicon Oasis** ≈ **MM minus Eggless, with drifted names**. Deltas:
  **category rename** ("Boxes" → "Mix Boxes" or accept the alias), **missing
  Eggless category**, **name normalisation** ("&" vs "and"), and a **stranded
  "Fudge Brownies = unavailable"** that MM would flip back on. Every one of these
  is a *write* the operator must approve per row.

### Foodics-integrated outlets (MM must route to Foodics, never the portal)
- **Barsha / Sharjah:** MM must **not** touch Talabat/Keeta/Deliveroo/Careem menus
  for these — writing there fights the GrubTech push. MM writes to the **Foodics
  `Grubtech` group + price tag** (§2c), which propagates. The writer is scoped to
  those two objects, so it is **structurally incapable** of touching the 85 café/POS
  items — the "surgical" requirement is enforced by *which object* it writes, not by
  a filter. Membership add/remove = add/drop from the category subgroup; price =
  price-tag entry kept equal to the product price. The Keeta re-categorisation
  ("New In") and its Unavailable state are **channel-local** and outside Foodics'
  push — the diff labels them "channel-only, informational," not "drift to fix."

### The counts, side by side
| Category | MM | Foodics | Talabat Karama | Talabat Barsha | Keeta Barsha | Careem DSO |
|---|--:|--:|--:|--:|--:|--:|
| Brownies | 9 | 18 | 9 | 9 | 9 | 9 |
| Cookies | 7 | 14 | 7 | 7 | 7 | ✓ |
| Cookie Melt | 8 | 8 | 8 | 8 | 8 | ✓ |
| Mix Boxes | 9 | 9 | 9 | 9 | 9 | "Boxes" |
| Cakes | 7 | 8 | 7 | 7 | **6** | ✓ |
| Desserts | 3 | 3 | 3 | 3 | 3 | ✓ |
| Eggless | 1 | 2 | 1 | 1 | 1 | **—** |
| Extras | 1 | 1 | 1 | 1 | 1 | ✓ |
| Ramadan | — | 2 | **2** | — | — | — |
| New In | — | — | — | **1** | **1** | — |
| Café/POS (11 cats) | — | ~74 | — | — | — | — |

Same brand, six surfaces, no two identical. That is the drift the feature exists to
close — and the reason every write has to be a reviewed, per-row decision.

---

## 4. Hours diff — the strongest case for shipping this

**Same kitchen (Barsha Heights), four surfaces, four schedules:**

| Surface | Sun | Mon | Tue | Wed | Thu | Fri | Sat |
|---|---|---|---|---|---|---|---|
| **Foodics** (single window) | 08:00–23:00 | " | " | " | " | " | " |
| **Talabat** | 08:15–22:00 | 08:00–22:00 | 08:00–22:00 | 08:00–22:00 | 08:00–22:00 | 08:15–22:00 | 08:15–22:00 |
| **Careem** | **17:00–22:00** | 08:00–21:45 | 08:00–21:45 | **CLOSED** | 08:00–21:45 | 08:00–21:45 | **12:05–21:45** |
| **Keeta** | 08:00–23:30 | " | " | " | " | " | " |

Closing times span **21:45 → 23:30**; Careem is **shut every Wednesday** and opens
five hours late on Sunday while the others open at 08:00. This is almost certainly
unintended divergence, and it is invisible today because nobody looks at four portals
side by side.

**The MM-side blocker:** MM's `Branch` model holds only a **single
`opening_from`/`opening_to` window** plus a whole-day `branch_holidays` table. It has
**no per-day, multi-shift weekly schedule** — so it cannot represent Careem's
"Wed closed, Sat 12:05" shape at all. **Before hours can sync, MM needs a canonical
per-day schedule model** (the plan's `BranchWeeklyHours`). Holidays map cleanly onto
each channel's own mechanism (Deliveroo *Days off*/events, Keeta *Special hours* +
Prayer mode, Talabat *Branch Status* pause, Careem disable-day / *Outlet Closed*).

Hours are **bounded and reversible** (a schedule, not a catalogue), high-value, and
already demonstrably broken — the right **first writer** once the model exists.

---

## 5. What the build must encode (failsafes the audit forces)

1. **Integrated branches: write only the Foodics `Grubtech` group + price tag.**
   Scoping the writer to those two objects makes it *structurally* unable to touch
   the 85 café/POS products — a stronger guarantee than a "mapped-rows-only" filter.
   Membership drives what appears; the price tag drives the price. (Deletion is
   allowed — remove from the subgroup.)
2. **Price-tag Price == product Original price, always in lockstep.** A reprice
   writes both the product and the price-tag entry (Product row *or* Modifier-Option
   row for variants). Surface the 3 current uplifts (Ramadan ×2, Christmas ×1) as
   policy violations to confirm/keep.
3. **Seed `sync_to_aggregators` from Grubtech group membership, not
   `sales_channels`.** Its own flag, default **off**; the audit proves
   `sales_channels` disagrees for 16 signature products.
4. **Deletion is allowed, but seasonal/channel-local items need intent.** Ramadan
   (Karama), New In (Barsha) are channel-local; with deletion enabled the diff must
   still let the operator mark "keep on channel" so a seasonal range isn't culled by
   a blanket mirror. Classify *missing-in-MM* as a proposed delete (approve per row),
   *missing-on-channel* + *value-mismatch* as add/update.
5. **Identity maps are per outlet — plus a Foodics group/price-tag map.** The same
   MM product has a different id on each outlet, and names have already drifted, so
   matching on name is unsafe. Seed a per-`(channel, branch, mm_kind, mm_id)` id map
   from a first read; for integrated branches also map `mm_product → Foodics
   product_id → Grubtech subgroup + price-tag entry`. (`external_item_map` is the
   template; it needs an outlet dimension.)
6. **Variant/"sizes" and composite ("pick-N") modifiers must round-trip.** MM's
   price lives in the quantity modifier; the diff compares the *effective* price set
   (incl. the price tag's Modifier-Options rows), not `base_price`.
7. **Route by integration status, not channel.** Integrated outlets → Foodics
   `Grubtech` group/price tag only; non-Foodics outlets → that portal's own tool.
   Writing an integrated outlet's menu on the aggregator fights the push (and Talabat
   blocks it anyway).
8. **Everything behind a flag, writes default off, Phase-1 writes are dry-run.**
   Same posture the ingest/GrubOps/Foodics-push integrations already take.
9. **Per-channel isolation + retry.** One outlet's failure must not block the rest
   (reuse the ingest's `_sweep_all` + advisory-lock + reauth-backoff pattern).

---

## 6. Recommended phasing (unchanged from the plan, now evidence-backed)

1. **Model + read + diff (safe, no writes).** Add `sync_to_aggregators`/
   `sync_channels` + the per-outlet identity maps + `BranchWeeklyHours`; give each
   channel a `fetch_menu(outlet)` (reusing the existing sessions/browser); build the
   cross-integrator diff + an admin drift report. Immediately useful — it surfaces
   exactly the drift in §3–§4 and catches the price/menu drift that has bitten
   before.
2. **Hours & holidays writer first.** Bounded, reversible, and already broken (§4).
   Fan out MM's new weekly schedule + holiday calendar to every channel's own
   editor/pause mechanism.
3. **Menu writer.** Integrated branches → Foodics API against the **`Grubtech` group
   (membership) + price tag (price, kept == product price)** only; non-Foodics
   outlets → that portal's own tool. Deletion enabled. Gated by the per-item
   `sync_to_aggregators` flag **and** an operator-approved diff; encode the Noon
   QC-submit and Deliveroo separate-Menus-login quirks. Confirm the Foodics API
   exposes group-membership and price-tag writes (vs UI-only) before committing to
   API-vs-automation for this leg.

---

## 7. Coverage & limitations of this audit

- **Directly read, item/price/hours level:** MM (production API, all 45), Foodics
  (131 products + 19 categories, **+ the `Grubtech` group's 9 subgroups + the
  `Grubtech` price tag Products & Modifier-Options tabs**), Talabat (Al Karama full
  menu + Barsha menu + Barsha hours), Careem (3 outlets + Silicon Oasis catalog +
  Barsha hours), Keeta (Barsha menu + Barsha hours).
- **Structure only (from the operations map), not re-read here:** Noon, Deliveroo
  item lists; per-outlet hours beyond Barsha; exhaustive per-item price parity
  (sampled, not enumerated). These are precisely the job of the Phase-1 read-side
  automation, which will enumerate every outlet × channel exhaustively and keep the
  drift report live.
- **MM branch hours** could not be read (production `/branches` is auth-gated); the
  finding that MM lacks a per-day schedule is from the model, not a live read.
- **No writes were made anywhere.** This document is the review gate before any code
  or schema lands.

---

# Per-aggregator verification + DB cross-check (2026-09-01)

Everything below is from a **verified source** — the live portal API/DOM, or the
**production database** (queried on the VM) — never assumed.

## Production DB — mappings (queried on `mm-backend`)

**Branches (4):** Al Karama (KRM), Barsha Heights (B001), Dubai Silicon Oasis (DSO),
Sharjah Kitchen (K001).

**`aggregator_branch_map` — 16 rows, COMPLETE, all verified against the live portal
store-switchers:**

| Channel | Outlets mapped (branch → outlet id) | Gaps (real, by design) |
|---|---|---|
| talabat | Sharjah 711571, Barsha 728173, Karama 793319 (brands 666733 / 666733 / 715778) | no DSO |
| careem | Barsha 1067984, DSO 1069463 (company 1026653, brand 1029671) | no Sharjah (shut, statusId 3), no Karama |
| keeta | Karama 1644336388, Sharjah 1644174206, DSO 1644170195, Barsha 1644189187 | — |
| noon | Sharjah MLTNGM1GBF, Barsha MLTNGM9FCH, DSO MLTNGMG2B1, Karama MLTNGMTB9M (brand R5967…, company PRJ135208) | — |
| deliveroo | Sharjah 693360, Barsha 693359, DSO 693361 | no Karama |

⇒ **Branch mappings need no seeding — they are complete and correct.**

**`external_item_map` — item mappings (products):**

| System | Products | matched to MM | approved | Options | matched | approved |
|---|--:|--:|--:|--:|--:|--:|
| grubops (Foodics) | 45 | 45 | **45** | 147 | 147 | **147** |
| keeta | 31 | 31 | 3 | 0 | — | — |
| noon | 19 | 19 | 0 | 33 | 0 | 0 |
| deliveroo | 16 | 15 | 1 | 8 | 0 | 0 |
| careem | 7 | 6 | 0 | 10 | 0 | 0 |
| talabat | 5 | 2 | 1 | 2 | 0 | 0 |

⇒ **GrubOps (the integrated-branch path) is fully mapped + approved.** The aggregator
product maps are the ingest's exact-name proposals — mostly *matched but unapproved*
(the approval gate is deliberate; approve in the item-mappings console). Six product
rows were unmatched name-variants; migration `172_agg_item_map_seed` resolves the
three unambiguous ones (serves 3-5 = 500 g; "[1 3 pieces]" → Fudge Brownies) and
leaves the three size-less Talabat cookie-melts for review. **All option maps are
unmatched** and need the 3/6/9-piece variants placed by hand — the next seeding step.

## Menu readers, per aggregator (verified source per channel)

| Channel | Menu source | Reader status |
|---|---|---|
| **Foodics** (integrated: Barsha, Sharjah) | Console `core-api` — the **Grubtech price tag** | ✅ **Verified live** — parser tested against the real 46-item price tag; full MM-vs-Foodics diff run on real data (found 5 real price mismatches). |
| **Careem** (Barsha, DSO) | REST `catalog-catalogs → catalog-categories → catalog-products` (bearer) | ✅ **Verified live** — real field shapes captured (`defaultPrice`, `status=="ACTIVE"`); parser tested against the real fixture. |
| **Talabat** (Sharjah, Barsha, Karama) | DeliveryHero `vendor-api` | ✅ **Verified from the VM session** — PerimeterX blocks in-browser reads, but the stored session carries the `authorization` bearer + `x-global-entity-id` and TLS impersonation passes PX. `/catalogs → /catalogs/<c>/categories/<cat>/products`; price = `unitPrice`, availability = `availability.available` & `active`. |
| **Noon** (all 4 + a 5th outlet) | RMS `/menu/details` | ✅ **Verified from the VM RMS session** — Akamai-gated in-browser, but the RMS `n-restaurantcode` session reaches it. `GET /menu/list` (the "Ext. grubtech" menus are Foodics-fed; the MM-managed one is read) + `POST /menu/details {menuCode}`; price = `price`, availability = `isActive AND NOT isOos`. |
| **Keeta** (all 4) | Portal `/m/web/product` | ⛔ Menu API requires an in-browser **H5guard `mtgsig`** signature per request — the stored session cookie cannot call it server-side (verified from the portal's own shell JS). **Browser-only**; a headed worker capture is the only path. Items known from the ingest map. |
| **Deliveroo** (Sharjah, Barsha, DSO) | separate **Menus** editor | ⛔ Menu is behind a **separate login** — "Edit menu" 302s to `/login?return_to=/menus/<id>`, and the sales session's hub `token` does not reach `rs-hub` (verified live). Needs that second session captured first. Items known from the ingest map. |

**Verified status: 4 of 5 aggregator menu readers work live** (Foodics, Careem,
Talabat, Noon — each captured from its real API via the browser or the VM session,
never guessed). The remaining two are **structurally headed-only**: Keeta signs every
request in-browser (H5guard), and Deliveroo gates its editor behind a second login —
both proven directly, not assumed. Their **item sets are already known** (the ingest
map above), so drift/seed work proceeds; their live menu readers wait on a headed
worker capture (Keeta) or the operator's Menus-editor session (Deliveroo).

**DB gap found:** the noon Menu-Maker lists a **5th outlet `MLTNGMQ677`** that is not
in `aggregator_branch_map` (which has the 4 known noon outlets). Worth reconciling —
either a new/renamed outlet or a stale listing.

## Option-map seeding — the honest finding

The aggregator **option** maps (the 3/6/9-piece variants) are **not reliably seedable
by name**, verified from the real prod rows:
- **noon** options are opaque ids (`i265138907b`, `i314442935b`, …) — no name to match;
  resolving them needs the noon menu read (id → name), which is Akamai-gated.
- **talabat** options are gram sizes ("250 grams", "500 grams") — but MM models those
  as separate *products* (Kinder Cookie Melt 250 g / 500 g), not modifier options, so
  there is no MM option to point at.
- the "N Pieces" options are shared across many products with **different prices**
  ("9 Pieces" = 125 for a brownie, 145 for a mix box, 135 for cookies), so a
  name-only match is ambiguous.

⇒ Option seeding is **blocked on the per-portal menu reads** (to recover each option's
product + price context). For the two channels with a menu reader (Foodics, Careem)
that context is available; for the anti-bot channels it waits on the headed reader.
This is stated rather than guessed — seeding options by name would map the wrong price.

## Seed conclusion

| Layer | State | Action |
|---|---|---|
| Branch maps (all 5) | Complete + verified | none needed |
| Product item maps | Matched (unapproved) + 3 variants resolved | approve in console; migration 172 |
| Ambiguous product maps (3 Talabat) | Size-less, ambiguous | human review |
| Option item maps | Opaque / modeling-mismatch / ambiguous | needs the menu reads first |

# Noon + Talabat mapping — resolved against the live menus (2026-09-01)

Ran the mapping resolver (`catalog_mapping.resolve_menu`, mirrored in a read-only VM
probe) over the **live** Noon and Talabat menus against the prod catalogue
(131 products, 19 categories, 46 option rows). Zero hallucination — every number
below is from the live read, not a guess.

**Talabat** (vendor 711571, the same catalogue on every outlet):
- **46 / 46 products matched** by exact name — fully resolvable, nothing ambiguous.
- 8 / 9 categories matched; the one miss is **"New In"**, a merchandising rail with
  no MM category (its items still map) — leave unmapped.
- **0 options** — Talabat models each size as its own top-level product, so there is
  no option layer to map. This retires the earlier "talabat gram-size options" worry:
  they are products, and they matched.

**Noon** (MM-managed menu `M3497661938091593085106254A`, 45 items):
- **43 / 45 products matched**. The two misses, both real variants:
  - `Kinder Cookie Melt (Serves 3-5)` → MM **`Kinder Cookie Melt (500 grams)`** (the
    same "serves 3-5 = 500 g" rule migration 172 applied for Careem/Deliveroo).
  - `Chocolate Caramel Crunch Cake` → MM **`Chocolate Caramel Crunch Cake Slice`**
    (MM carries only the slice; a human confirms slice-vs-whole in the console).
- 7 / 9 categories matched; misses are **"New In"** (merch rail) and **"Cake"** →
  MM **"Cakes"** (singular/plural).
- **90 / 108 option instances matched** by (name, price). The 18 misses are all the
  box-filling flavours where **Noon uses the plural** ("Cookies and Cream Cookies")
  and MM the singular ("Cookies and Cream Cookie"), both at price 0.00 — the same
  option, name-drifted. They surface as proposals for a one-click console map.

The earlier "options are not seedable by name" finding stands for the anti-bot
channels *without* a reader; now that Noon and Talabat both read live, `resolve_menu`
matches options by **name + price** and only the plural/singular drift is left. The
`resolve` admin action approves every exact match and leaves the genuine variants as
proposals — the mapping is figured out, not guessed.

# Creating menu items — the mechanism, and how the mapping is stored (2026-09-01)

**Discovered, not assumed.** Read the Foodics console's own API client live: the
CRUD verbs are `getting`(GET) / **`creating`(POST)** / `updating`(PUT) /
`deleting`(DELETE) — *not* `inserting` (absent from every bundle). Create is
`POST /core-api/creating` with the same `{url, payload}` envelope as `updating`.

**Foodics is the create path for the two integrated branches — one create, not five.**
A product created in Foodics, placed in its **Grubtech category subgroup** and given a
**Grubtech price-tag price**, is pushed by Foodics to *every* marketplace. Verified
structure (read live):
- Grubtech parent group → **9 category subgroups** (Cakes `a05d8176…`, Cookies
  `a063495f…`, Brownies `a0634a39…`, Cookie Melt `a05d8155…`, Mix Boxes, Eggless,
  Desserts, Extras, New In) — mirroring the MM categories.
- A product carries `groups:[{id, pivot:{is_active}}]` (membership) and
  `price_tags:[{id, pivot:{price}}]`. On a live Grubtech product the price-tag price
  equalled the base price — **strict parity, confirmed in the wild**.
- A create needs `category_id` (the menu category, distinct from the subgroup),
  `tax_group_id` (UAE VAT `a03ed56a…`) and `pricing/selling/costing_method` (1/1/2) —
  read live and echoed, or Foodics rejects the product.

`foodics_provider.create_product(...)` builds exactly this (price-tag price defaults to
the product price — parity is not optional); `catalog_sync.create_menu_item(...)` is the
gated entry point (`CATALOG_SYNC_ENABLED`), **dry-run by default** — it returns the
exact create it would POST and mutates nothing.

**Storing the mapping once created.** The same `resolve_menu` that figured out the
existing mappings is the storage mechanism: after a Foodics create, the marketplaces
show the item under the same name on their next menu read, and `resolve_menu` records
each channel's `external_item_map` (name → product) automatically. For Foodics itself,
`create_menu_item` records the row inline from the create response (the returned Foodics
id → product, approved). No parallel table, no per-channel create.

**Non-Foodics outlets** (Al Karama, Silicon Oasis) still need a direct-portal create —
a later phase. `create_menu_item` refuses those targets with a clear message rather than
POSTing an unverified payload.

# Autonomy + the remaining open points (2026-09-01)

## Autonomous sweep — DONE (commit bc913b95)

The catalog sync now runs unattended on the **same infrastructure as the ingest** —
no new scheduler, no cron, no second worker. `run_catalog_sync_scheduler_forever`
mirrors the rolling-sales loop (wall-clock honest, DB-backed boot catch-up off the
freshest snapshot's `fetched_at`, per-tick isolation) and is registered as a third
child under the ingest's leader election, so exactly one API slot ticks and blue/green
hands it over for free. `run_catalog_sync_once` refreshes each target's menu/hours,
refreshes the mapping proposals, and stores the drift — per-target isolated,
commit-per-target. It approves mappings only when writes are on; reads-only leaves them
proposals. Gated by `CATALOG_SYNC_SWEEP_MINUTES` (0 = off default; W9 five-place).

## Creation, per channel — the honest state

| Channel | Create path | State |
|---|---|---|
| **Foodics** (→ all 5, integrated branches) | `POST /core-api/creating` product + Grubtech subgroup + price-tag | **Built + verified** (verb, subgroups, methods read live); dry-run gated |
| **Careem / Talabat / Noon** (non-Foodics outlets) | Extend the reader's session-replay to a **POST** (reuse TLS-impersonation, no browser) | Endpoint is the POST sibling of the read; **exact payload unverified** — confirming it needs one controlled create, so not shipped as a guess |
| **Keeta / Deliveroo** | Headed-worker browser action (Keeta H5guard, Deliveroo separate login) | Needs a new worker `JobKind` + dispatch + trigger (the worker's action set is a fixed enum — recon-confirmed); not built |

The two integrated branches — where automated sync actually matters — are fully covered
by the Foodics master path. Direct-portal creation only affects the non-Foodics outlets,
which are hand-edited today.

## Hours readers — Careem shape verified, day-origin is the open question

`_HOURS_READERS` is still empty. Careem's `food-outlet-operational-hours` was read live
2026-09-01 and returns a clean weekly shape:
```
[ {"day": 1..7, "active": 0|1, "shifts": [{"start_time":"HH:MM:SS","end_time":"HH:MM:SS"}]} , ... ]
```
`active:0` = closed that day; split shifts are multiple entries. **The one thing not
yet pinned is the day-origin** (is `day 1` Saturday, Sunday or Monday?). MM stores only a
single daily `opening_from`/`opening_to` — no per-weekday closed day — so our own data
can't disambiguate it, and the live data only *hints* (a late `12:05` open on `day 7`
looks like a UAE Friday, which would make `day 1` = Saturday). Mapping `day → weekday`
(MM's `0=Sun…6=Sat`) on a hunch is exactly the silent bug the canon warns against — a
wrong origin would eventually close a branch on the wrong day — so the reader waits on a
one-time confirmation of the origin (the Careem portal labels the days, or the operator
states their trading week).

## The decision that unblocks the rest

Everything left forks on a choice that is genuinely the operator's, because it touches a
live production menu or is a large build:
1. **Direct-portal creates (Careem/Talabat/Noon):** confirm the create payload by one
   controlled *create-then-immediately-delete* per channel (a brief live mutation), which
   captures the exact request — then the gated writers ship verified. Reuses the existing
   session replay; no browser.
2. **Keeta/Deliveroo creates + Keeta/Deliveroo menu reads:** build the headed-worker
   action (new `JobKind`), the only way past H5guard / the separate Deliveroo login.
3. **Hours:** confirm the Careem day-origin, then the hours readers (Careem, then
   Talabat/Noon) ship and feed the same autonomous sweep.
