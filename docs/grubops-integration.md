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
| Availability writes | `https://internal-api.grubtech.io` | `GRUBOPS_API_BASE` |
| Brands and menu listing | `https://api-grubone.grubtech.io` | `GRUBOPS_CATALOG_API_BASE` |

## Endpoints in use

```
POST {api}/item-availability-mgt/v1.0/items-availability/unavailable
POST {api}/item-availability-mgt/v1.0/items-availability/available
POST {api}/item-availability-mgt/v1.0/items-availability/byItems
GET  {api}/location-mgt/v1.0/locations/search/byPartnerId?partnerId=…
GET  {catalog}/partners/{partnerId}/locations/{locationId}/serving-brands
```

Their services answer a rejected payload with **HTTP 200 and an `errorCode` in
the body**, so `_unwrap` checks both and neither alone is enough.

## Open: confirm the menu listing and the write body

Two things are still inferred from the compiled Flutter bundle rather than
observed, and both are isolated so confirming them is a small change:

1. **The menu listing.** `search_menu_items` / `search_modifiers` currently
   return 500 from `api-grubone`, so the item half of "Sync from GrubOps"
   cannot populate yet. The route exists — a 500 rather than a 404 says the
   path is right and the query is not.
2. **The exact write body.** Field *names* are recovered from the bundle's
   mappers and are believed correct; the full shape, and the format
   `unavailableTill` wants, are not confirmed against a real request.

**To confirm both:** open the GrubOps console signed in as the service user,
open the browser's network tab, mark one test item out of stock and put it
back. Record the two XHRs to `items-availability/*` and the request the item
list makes. Then adjust `grubops_service.unavailable_body` /
`available_body` and the two `search_*` methods on the provider to match.

Until then, leave `GRUBOPS_SYNC_ENABLED` off. Everything else — login, token
refresh, location discovery, the branch switch, the reconcile diff — is working
and tested.

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
4. Work through the queue. Fuzzy matches show a score; anything below about 95%
   is worth reading twice. Correct an id inline if the guess is wrong — that
   marks the row `manual` and the matcher will not touch it again.
5. Turn on the branches whose registers are live.
6. Set `GRUBOPS_SYNC_ENABLED=true` and redeploy. The loop picks it up within
   `GRUBOPS_RECONCILE_TICK_SECONDS` (120 by default).

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
