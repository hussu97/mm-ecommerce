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

**`unavailableTill` must be spelled `...Z`, not `+00:00`.** Their client is
Dart and sends `toUtc().toIso8601String()` — `2026-08-23T13:00:52.213Z`.
Python's `isoformat()` writes the same instant as `+00:00`, and their service
accepts it, stores it, and mangles it: `13:00:52+00:00` was read back as
`02:00:00Z`, eleven hours early with the seconds discarded and the moment
already past, so an hour-long out-of-stock arrived as one that had already
lapsed. Nothing rejected it and nothing logged it — the only way to see it was
to read the value back. `_iso8601_z` in `grubops_service` is the fix and the
test carries the real numbers.

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
