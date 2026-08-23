# GrubOps out-of-stock sync

Marking an item out on the register takes it off Noon, Talabat and Deliveroo,
instead of somebody having to say it a second time in the GrubOps console.
One way only: this app is the source of truth and GrubOps is told.

## Where things are

| Piece | File |
|---|---|
| Transport, Cognito login, token cache | `apps/api/app/services/providers/grubops_provider.py` |
| Payload building, the two write paths | `apps/api/app/services/grubops_service.py` |
| The reconcile loop | `apps/api/app/services/grubops_reconcile.py` |
| Name matching, location discovery | `apps/api/app/services/grubops_mapping.py` |
| Tables | `apps/api/app/models/grubops.py`, migration `131_grubops_mapping` |
| Console API | `apps/api/app/api/v1/grubops.py` |
| Console screen | `apps/admin/app/(dashboard)/grubops/page.tsx` |

## How it authenticates

GrubTech publish no partner API. GrubOps 2.0 is a Flutter web app talking to
`internal-api.grubtech.io`, and this signs in the way the console does:

- AWS Cognito, `eu-west-2`, flow `USER_PASSWORD_AUTH`, app client
  `2d8lmtmc241sviat2psomuuon8`, pool `eu-west-2_CKectL0Mu`. No MFA.
- The resulting **id token is used directly as `Authorization: Bearer`**. The
  console's second hop through `admin-user-auth/user-authentication/authenticate`
  turns out to be unnecessary for these services — the id token already carries
  `partner_id`, `brandIds` and `locationIds`, which is what they check.
- It lasts an hour. The cache refreshes two minutes early, and a 401 is retried
  once with a fresh token, which is what covers a sweep that began at minute 59.

Verified against the live account on 2026-08-23.

## Hosts

GrubTech split these, so there are two bases rather than one:

| What | Base | Setting |
|---|---|---|
| Availability writes, locations, the item list | `https://internal-api.grubtech.io` | `GRUBOPS_API_BASE` |
| `serving-brands`, and only that | `https://api-grubone.grubtech.io` | `GRUBOPS_CATALOG_API_BASE` |

Almost everything is on the first. The second exists because `serving-brands`
answers there and 404s on the main host, and every availability payload needs
the `brandId` it returns — so one endpoint earns a whole second base.

## Endpoints in use

```
POST {api}/item-availability-mgt/v1.0/items-availability/unavailable
POST {api}/item-availability-mgt/v1.0/items-availability/available
POST {api}/item-search/v2.0/items/                     ← the menu, with availability
GET  {api}/location-mgt/v1.0/locations/search/byPartnerId?partnerId=…
GET  {catalog}/partners/{partnerId}/locations/{locationId}/serving-brands
```

**The item list is a POST, and that is the whole trick.** Their bundle also
contains `GET /v2.0/menu-items/searchMenuItem/byLocation/{loc}/byBrands/{brands}`
and a matching `…/modifiers/searchModifier/…`; both are dead ends — 404 on the
item-search service, 500 on the catalogue host, at every combination of query
we tried. The console uses `POST /v2.0/items/` with

```json
{"partnerId": "…", "locationId": "…", "brandIds": ["…"], "searchText": "",
 "itemType": "RECIPE", "unavailableItemsOnly": false, "language": "en"}
```

and gets back `{"items": [...]}`, each item carrying `{id, type, name.translations,
parentAssociations, childAssociations, unavailability, attributes}`.
`itemType` is `RECIPE` or `MODIFIER` — two calls. `unavailableItemsOnly: true`
makes the same call an availability read, which is why nothing here uses
`items-availability/byItems`: that endpoint rejects every body we could build
for it, and this returns the same facts plus the names the seeder needs.

Note that `unavailability` on a **response** is
`{source, reason, status, unavailableUntil}`, while the **request** that sets
it takes `{unavailableTill, unavailableReason}`. Different spellings for the
same two ideas; both are in their bundle's mappers.

Their services answer a rejected payload with **HTTP 200 and an `errorCode` in
the body**, so `_unwrap` checks both and neither alone is enough.

## How the two catalogues are paired

Verified against the live account and a copy of the production catalogue:
**45 products and 147 options, every one an exact name match, one-to-one, at
both locations, with nothing unmatched on either side.** No fuzzy guesses were
needed at all — but the threshold and the review queue stay, because the next
new cake is the one that will need them.

Getting there took one non-obvious step. Names alone do not identify a
modifier: GrubOps duplicates a modifier group **per recipe**, so "Your Choice
of Quantity" exists seventeen times and the modifiers under those copies share
three names between them. Matching on (group, name) looked perfect — every
match "exact" — while quietly collapsing 147 options onto 51 modifiers.

The fix is to pin a modifier by the **recipe above its group** as well as the
group: `MODIFIER → MODIFIER_GROUP → RECIPE` in their `parentAssociations`, and
`ModifierOption → Modifier → ProductModifier → Product` in ours. Our catalogue
duplicates groups exactly the same way — seventeen groups of that name, one per
product — which is what makes the pairing one-to-one. There is a test for this
specific trap in `test_grubops_sync.py`.

Our "Size" group (73 options) has no counterpart: GrubOps models size as
separate recipes, "Cookie Melt (250 grams)" against "(500 grams)". Those
options are reported unmatched, which is correct.

## The writes, confirmed

Sent against the live account on 2026-08-23 and verified by reading the state
back. **The two endpoints do not take the same shape**, which is the thing
worth knowing before touching either.

`unavailable` takes the identity in an envelope:

```json
{"itemInfo": {"partnerId":"…","locationId":"…","brandId":"…",
              "recipeId":"…","modifierId":null,"childModifierId":null,
              "type":"RECIPE"},
 "source": "grubOps 2.0",
 "status": "UNAVAILABLE_UNTIL_FURTHER_NOTICE",
 "unavailabilityInfo": {"unavailableTill": null, "unavailableReason": null}}
```

`available` takes the bare identity, **flat, with no `source`**:

```json
{"partnerId":"…","locationId":"…","brandId":"…","recipeId":"…",
 "modifierId":null,"childModifierId":null,"type":"RECIPE"}
```

Send the envelope to `available` and every field of the identity comes back as
`must not be null`, because they are being looked for at the top level.

**We write what their console writes.** `source` is `grubOps 2.0` — the GrubOps
app name, the string already stamped on every record on the account — and
`unavailableReason` is null, because their client hardcodes it null and offers
no way to set it. A record of ours is therefore indistinguishable from one
somebody made in the console.

That is deliberate, and it costs nothing. The tempting alternative was a marker
of our own so our writes were identifiable; it would also have made them
*different* — an unknown source, on a private API with no contract, in a field
their UI renders. Nothing here needed the marker: the reconcile loop takes
desired state from our own database and never adopts theirs, so it cannot
mistake their writes for ours however they are labelled.

**A modifier must carry its recipe.** `{"recipeId": ["must not be null"]}` is
the answer to a `type: MODIFIER` write that names only the modifier. So
`grubops_item_map` stores the parent recipe on option rows as well as the
modifier's own id — the same pairing the matcher needed, for the same reason.

**GrubOps cannot hold a return *time*, so we never send one.** `unavailableTill`
is date-only in practice: their service keeps the day and discards the clock,
substituting 02:00Z — 06:00 in Dubai, the day-start for these outlets.
Measured against the live account:

```
sent 2026-08-26T12:19:11Z  ->  stored 2026-08-26T02:00:00Z
sent 2026-08-23T12:49:11Z  ->  stored 2026-08-23T02:00:00Z
sent 2027-08-23T12:19:11Z  ->  stored 2027-08-23T02:00:00Z
```

Their own console only offers "until next day", so a date is all it has ever
needed. Ours offers an hour — and an hour becomes *this morning at six*, a
moment already past, so the item never leaves the aggregators. A one-hour
out-of-stock was silently a no-op.

So every out-of-stock goes out as `UNAVAILABLE_UNTIL_FURTHER_NOTICE`, whatever
the terminal said, and **the reconcile loop owns the clock**: it recomputes
effective availability every tick and pushes the return the moment
`out_of_stock_until` lapses. A tick's granularity, against a field they would
have rounded to the nearest morning anyway — and it fails in the safe
direction, since an app that stops pushing leaves items out rather than
letting them return on GrubOps' clock with nobody watching.

(This also retires an earlier theory. The first symptom was a mangled
timestamp, which looked like a formatting problem — Python writes `+00:00`
where Dart writes `Z` — and matching the spelling changed nothing. The clock
was never being read.)

**Their writes are patch-like on an existing record.** Creating an
unavailability stores exactly what is sent; *updating* one ignores a null, so a
field that already has a value cannot be cleared by sending null for it — an
empty string works, and only clearing the record entirely gets back to null.
This matters for nothing in normal operation, because the ordinary case is an
item going out of stock, which creates a record and stores our null reason
verbatim. It matters a great deal when tidying up after a test.

**Putting back something already back is a 400**, with the message
*"is/are not currently marked as unavailable in the database"*. That is not a
failure — it happens when somebody clears the item in the GrubOps console
between two ticks, and the end state is the one we wanted — so `push_deltas`
records it as pushed rather than retrying it for ever.

**Their reads lag their writes** by a second or two. Anything that writes and
then verifies has to retry the read; a single read straight after a write shows
the previous state and will convince you the write failed.

None of this changed the schema. `grubops_item_map` already held
`grubops_recipe_id`, `grubops_modifier_id`, `grubops_child_modifier_id` and
`grubops_type` as separate columns, which is exactly the identity GrubOps
takes.

## Why it is a loop and not a push

Out-of-stock **expires on read**: `availability_service` compares
`out_of_stock_until` with now every time the question is asked, so an item
marked out until close is on sale again the moment the clock passes, and
*nothing fires when that happens*. A push-on-change integration would therefore
send the "out" half of every timed window and never the "back" half.

So the authority is a loop that recomputes and diffs. A lapsed hour is not an
event to catch, it is a different answer to the same question, and the next
tick sees it. It also self-heals drift: a dropped push, a deploy mid-window, or
somebody editing availability in the GrubOps console by hand.

The immediate push beside each write is only there so the aggregators do not
wait two minutes. It is allowed to fail.

## Switching branches on

Sync is per branch, in the console under **GrubOps → Branches**, and every
branch is discovered **off**.

That is a real decision, not a formality: a branch whose staff are not marking
things out on the terminal has nothing true to say about its stock, and syncing
it would push a confident "everything is available" over whatever that counter
maintains in GrubOps by hand. Today the register is live in Sharjah and not in
Barsha Heights, so Sharjah is on and Barsha is not.

Turning a branch **off** stops sending and leaves GrubOps holding what it last
heard — deliberately, because the alternative is a switch that silently puts a
shop's whole menu back on sale.

## Turning it on

1. Deploy with `GRUBOPS_SYNC_ENABLED=false` (the default).
2. Set `GRUBOPS_USERNAME` / `GRUBOPS_PASSWORD` as GitHub secrets and redeploy —
   a secret only reaches the containers once the deploy rewrites the VM's `.env`.
3. In the console, press **Sync from GrubOps**. Branches appear under the
   Branches tab; item suggestions appear under Needs approval.
4. Work through the queue. Against today's catalogue every row comes out
   `exact` — 45 products and 147 options, measured — so this is a read rather
   than a repair. Fuzzy matches show a score; anything below about 95% is worth
   reading twice. Correct an id inline if a guess is wrong: that marks the row
   `manual` and the matcher will not touch it again.
5. Turn on the branches whose registers are live.
6. Set `GRUBOPS_SYNC_ENABLED=true` and redeploy. The loop picks it up within
   `GRUBOPS_RECONCILE_TICK_SECONDS` (120 by default).

The first tick after switching a branch on sends its whole approved map, since
nothing has been pushed for it yet. Expect one burst and then near-silence:
after that only differences go.

Nothing is ever sent for an unapproved row, so steps 1–4 are safe to do at
leisure with the flag off.

## The risk, stated plainly

This rides a private, undocumented API and a real user login. It can break
without notice, and GrubTech have made no promise about any of it. The
mitigations are that it is behind one provider, one flag and one switch per
branch; that every failure is a log line rather than something a customer or a
cashier waits on; and that the reconcile loop makes a broken spell self-correct
once it is fixed.

**Official partner access is the sustainable version of this.** Worth asking
GrubTech for; the provider is the only file that would need to change.

---

# GrubOps order ingest (the return path)

The OOS sync above pushes availability *out*. This pulls aggregator orders
(Talabat, Noon, Careem, Deliveroo, Keeta) *in*, recording each as an ordinary
`orders` row with `source = 'aggregator'` so MM is the single book of record for
counter, website and aggregator sales — and so an aggregator order lands on the
MM POS board, alarms, and prints in MMPOS styling exactly like a website order.

GrubTech is wired into Foodics: Foodics auto-accepts and marks orders packed,
and the aggregator's own rider delivers. MM does not take that over — it
records, displays, decrements stock, and can mirror a cancel/complete back.

## Where things are

| Piece | File |
|---|---|
| Provider order methods (read + force-* write-back) | `apps/api/app/services/providers/grubops_provider.py` |
| Ingest: create the order, map lines, decrement stock, walk the lifecycle, write-back | `apps/api/app/services/grubops_orders_service.py` |
| The ingest poll loop | `apps/api/app/services/grubops_orders.py` |
| Register attach (pending + check number) | `pos_order_service.attach_aggregator_order` |
| Tables | `apps/api/app/models/grubops_order.py`, migration `132_grubops_orders` |
| Monitoring API | `GET /grubops/orders` in `apps/api/app/api/v1/grubops.py` |
| Monitoring screen | the "Ingested orders" tab on the GrubOps admin page |
| iPad (mm-pos) | `PosOrder.isAggregator` / `aggregatorChannel`, receipt channel line, kitchen filter |

## How orders come in

There is **no webhook** — GrubTech does not push, its console polls, and its
realtime is AppSync (GraphQL subscriptions we do not subscribe to). So ingest is
a poll loop, `GRUBOPS_ORDERS_TICK_SECONDS` (60s), on advisory lock
`0x6D6D_4241_5443_4804`, storefront app only.

Each tick: `getOrderSummaryList` for the live statuses (a single most-recent
window — `page > 0` is a 404, and an empty result is HTTP 200 with an
`errorCode` of 404). For any order new or whose status moved, `getOrderInfo` for
the full detail, then `grubops_orders_service.ingest`.

Order lines carry GrubOps `recipeId` / `modifierId`, which resolve to our
`product_id` / `modifier_option_id` through the **same approved
`grubops_item_map`** the OOS sync maintains — validated 17/17 recipes and 20/20
modifiers across a day of real orders, zero unmapped. An unmapped line is
recorded (name kept, `product_id` null) and counted, never dropped.

Money is taken **verbatim** from GrubOps — the aggregator priced and charged it,
there is no cart to re-price, and re-pricing would raise on a delivery address
GrubOps records as "Unknown". `subtotal` is null on cash (POSTPAID) and some
prepaid orders, so it falls back to `unitPrice`.

## Status: two axes

- **MM lifecycle** (`orders.status`) mirrors GrubOps, walked one honest rung at
  a time (created → confirmed → packed → delivered) so the timeline reads true,
  every move attributed `aggregator` at GrubOps's own timestamp. On-hold is not
  a move of ours; a cancel is attempted directly.
- **Register** (`orders.pos_status`): `pending` at ingest (so it alarms and
  prints on accept, via `notify_order_placed`), `active` when a cashier or the
  auto-accept takes it, and `closed`/`void` when GrubOps finishes/cancels it.

An aggregator order is deliberately **not** treated as `online`: `_mm_owns_fulfilment`
gates off the courier, batching and refund machinery (the aggregator owns
delivery and money), and `email_service` suppresses the customer email (the
aggregator already sent one). Stock **is** decremented like a website order, so
the OOS→GrubOps menu sync reacts to aggregator sales automatically.

## Write-back (mirror out)

Cancelling or completing an aggregator order **in MM** mirrors to GrubOps via
`order-force-cancel` / `order-force-complete` — but only if the order's live
`orderManagementOptions` still allow it, else it records `last_push_error` and
leaves GrubOps alone. Guarded against a feedback loop: the ingest loop's own
moves are attributed `aggregator` and never mirror back out; only a move from
our side (an admin, the till) does.

## The accept-flow ceiling (why we do not accept from MM)

The GrubOps console API has **no accept/prepare** endpoint — only the three
force-* overrides. The normal accept/start/prepare actions live in the **KDS**
app behind a *separate* Cognito user pool (a per-station login), which our
console token cannot reach. The KDS web app (`grubkds.grubtech.io`) is moreover
decommissioned: it bootstraps its config from AWS SSM with IAM keys embedded in
its bundle, and those keys are now rotated (`UnrecognizedClientException`), so it
cannot even start. Foodics' own server-side scheduler auto-accepts regardless of
the POS toggle. **Accepting an order from MM is therefore not reverse-engineerable
— it needs official GrubTech partner API access.** Until then, MM records and
displays (and Foodics keeps accepting/preparing), and force-complete/cancel are
the only writes our token can make.

## The order-data audit (per-channel), and what it changed

A day of live orders across all five channels was audited. The findings, and how
the ingest now handles each:

| Field | What GrubOps sends | How MM handles it |
|---|---|---|
| Channel | `orderHeader.foodAggregatorName` / `sourceDisplayName` ("Talabat", "Keeta 2.0", "Noon", "Deliveroo", "Careem"); stable slug in `channelId` | Stored on `orders.aggregator_channel`; normalised to a courier code by `courier_catalog.code_for_channel` |
| **Two order ids** | a long marketplace `externalId` **and** a short driver-facing code — but the short code lives in a *different place per channel* | Both stored: the long id on `external_reference`, the short one derived at ingest onto `aggregator_display_code` and printed in its place |
| Delivery fee | `deliveryTotalPrice` — the charge the **customer** paid the marketplace; **not** part of `totalPrice` (verified: `total == net + tax`) | Kept out of `orders.delivery_fee` (which stays 0, so no sales/freight report counts it); recorded on `orders.aggregator_delivery_fee` for the receipt only |
| Notes | `orderHeader.instructions` (order-level; no per-line notes populated) | On `orders.notes`, already printed on the receipt and kitchen ticket and shown on the incoming card |

### The driver-facing pickup code

There is **no single field** for it — each marketplace surfaces it differently —
so `grubops_orders_service._driver_code` takes the surest available, in order:

1. a short code embedded in the instructions (**Talabat**: `"…short code: 1445"`);
2. the external id when it is already short and numeric (**Noon** `5717`,
   **Deliveroo** `0037` — for these the external id *is* the customer's number);
3. the GrubOps sequence number, which the console shows the counter for a
   **Keeta**/**Careem** order whose own id is a long machine string;
4. the last four of the external id, as a last resort.

The long marketplace id always stays on `external_reference`; only the short code
is printed. On the register this reuses the existing `courier_reference` →
`foreignOrderNumber` path, so the driver reads "1445", not a 16-digit id, with no
app change to the print logic.

## Aggregators as couriers (identification on every surface)

The five marketplaces are seeded as `couriers` rows flagged `is_aggregator`
(migration 133), alongside a `logo_url` on every courier. `courier_catalog`
resolves an order to a `CourierBadge` (code, name, logo) — from the channel for
an aggregator order, from the fulfilment provider for a website one — which the
POS board/cards, the admin order list and the fulfilment panel all render. The
logo URL is served from the `couriers` table (cached in-process), so a logo can
be swapped in the database without shipping an app. `noon_food` (the marketplace)
and `noon_send` (the courier) are kept distinct, with different codes and logos.

**Logos** live in the images bucket (Cloudflare R2) under a dedicated
`couriers/` prefix, separate from `products/`. They are generated as uniform
256×256 brand-colour badges in `scripts/courier_logos/` and pushed with
`python -m scripts.upload_courier_logos` (run once where the R2 credentials are
set — the production VM). To replace one with a real trademarked logo later,
drop a new 256×256 PNG over `scripts/courier_logos/{code}.png` and re-run; nothing
else changes because `couriers.logo_url` already points at that key.

## Admin: a marketplace order is read-only

An aggregator order is fulfilled and delivered by the marketplace and its status
is mirrored in from GrubOps, so the admin order-detail page hides the fulfilment
actions (mark packed / delivered / undelivered / cancel) for it and shows a
read-only marketplace panel (channel logo, pickup code, external ref, the
customer's delivery fee) instead. The order list gains an **Aggregator** channel
tab and a **courier** filter, and renders the marketplace logo in the channel
column. The GrubOps console gains search and alphabetical sort across its tabs.
