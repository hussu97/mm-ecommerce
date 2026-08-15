# Umami Analytics Setup — Melting Moments

This file tracks every manual configuration that must exist in the Umami dashboard.
Update it whenever `apps/web/lib/analytics.ts` changes.

---

> **Every event in this file also goes to Microsoft Clarity.** Since 2026-08-10,
> `track()` in `apps/web/lib/analytics.ts` feeds both tools from one call, so
> anything added below reaches Clarity automatically as a filterable event —
> there is no second list to maintain. What Clarity does with an event, which
> payload fields become filters and which are deliberately withheld, is in
> [`docs/microsoft-clarity-setup.md`](microsoft-clarity-setup.md). Umami remains
> the source of truth for **counts**; Clarity answers **why**, on the subset of
> sessions it is not blocked from recording.

## Custom Events Reference

All events are fired via `window.umami.track(name, data)` from `apps/web/lib/analytics.ts`.
Events fired before the Umami script finishes loading are queued briefly and retried.
Umami records them automatically — no dashboard config needed for events to appear under the **Events** tab.

The admin dashboard reads them back through `GET /api/v1/analytics/traffic`
(`apps/api/app/api/v1/analytics.py`) and lists them under **Storefront Events**.
That read needs `UMAMI_API_KEY` and `UMAMI_WEBSITE_ID` on the API, and an Umami
Cloud plan that includes API access — see Troubleshooting below.

### Conventions

Phase 4 introduced four rules that every event added from now on should follow.
They exist because the dashboard is only as useful as its ability to *group*.

- **Every money event carries `currency: 'AED'`.** A number with no unit on it is
  a number somebody will eventually read as dollars.
- **Every failure carries a `reason`** — a short, stable slug from
  `failureReason()` in `apps/web/lib/analytics.ts`, never the message the
  customer saw. Error copy is translated, is rewritten whenever the wording is,
  and arrives from the API as a full sentence; recorded verbatim it produces one
  distinct value per incident, which is a column you cannot group by.
- **Every event that can happen in more than one place carries `surface`** —
  `pdp`, `tile`, `modal`, `cart`, `checkout`, `account`, `footer`, `faq`, …
- **Empty properties are dropped before sending.** `null`, `undefined` and `''`
  never reach Umami, so an optional field that is absent does not become a
  permanent empty column.

No event carries personal data: no email, no phone, no address line, no
coordinates.

### Happy path

| Event | Payload fields | Phase | Fired from |
|---|---|---|---|
| `select_promotion` | creative (hero\|promo_banner\|category_tile\|cater\|usp), slot, title, target | phase 4 | PromotionLink.tsx (HeroCarousel, PromoBanners, CategoryTiles, CaterSection) |
| `select_item` | product_name, list, position, price, currency | phase 4 | ProductCard.tsx (every listing), FeaturedProducts.tsx (its own card) |
| `sort_products` | sort, surface, category | phase 4 | SortSelect.tsx ([category] and all-products) |
| `view_category` | category_name, product_count | phase 2 | CategoryTracker.tsx (client wrapper in [category]/page.tsx) |
| `search` | query, result_count, has_results | phase 2 | SearchTracker.tsx (client wrapper in search/page.tsx) |
| `view_product` | product_name, category, price, has_modifiers, slug, in_stock, currency | phase 1 | ProductDetailATC.tsx (on mount) |
| `modifier_selected` | product_name, group_name, option_name, price_delta | phase 4 | ModifierSelector.tsx (a pick that sticks, never the preselected default) |
| `options_modal_opened` | product_name, entry (select_options\|add_more) | phase 4 | AddToCartControl.tsx |
| `add_to_cart` | product_name, variant_name, price, quantity, value, surface, currency | existing + phase 4 | ProductDetailATC.tsx, AddToCartControl.tsx (every tile), ModifierModal.tsx |
| `update_cart_quantity` | product_name, from, to, direction, surface | phase 4 | cart/page.tsx, AddToCartControl.tsx |
| `remove_from_cart` | product_name, surface | existing + phase 4 | cart/page.tsx, AddToCartControl.tsx (stepping a tile to zero) |
| `view_cart` | item_count, subtotal, has_promo, currency | phase 4 | cart/page.tsx (once per visit, not per mutation) |
| `coupon_tray_shown` | code, percent, first_orders_limit | phase 4 | NewCustomerCouponTray.tsx (the impression, so the tray has a denominator) |
| `coupon_banner_shown` | code, percent | phase 5 | PromoBanner.tsx (the homepage strip, once per undismissed visit) |
| `coupon_banner_clicked` | code | phase 5 | PromoBanner.tsx (the tap; numerator to the impression above) |
| `promo_applied` | code, discount, surface, subtotal, from_tray, currency | existing + phase 4 | cart/page.tsx, PromoCodeStep.tsx |
| `begin_checkout` | item_count, subtotal, has_promo, currency | existing + phase 4 | cart/page.tsx |
| `view_checkout` | item_count, subtotal, is_guest, has_saved_address, currency | phase 4 | checkout/page.tsx (once per visit) |
| `saved_address_selected` | surface | phase 4 | AddressModal.tsx |
| `address_saved` | surface, has_pin, is_new | phase 4 | AddressModal.tsx, account/addresses/page.tsx |
| `address_deleted` | surface | phase 4 | AddressModal.tsx, account/addresses/page.tsx |
| `location_pin_set` | method (autocomplete\|map_tap\|drag\|current_location), surface | phase 4 | LocationPicker.tsx |
| `select_delivery_method` | method, fee, currency | phase 3 | checkout/page.tsx (the delivery/pickup toggle) |
| `pickup_branch_selected` | branch_name | phase 4 | checkout/page.tsx |
| `delivery_quote` | serviceable, delivery_fee, base_fee, free_applied, free_available, free_threshold, subtotal, currency | phase 4 | checkout/hooks/useOrderPreview.ts (each settled `POST /orders/preview` with a pin behind it) |
| `free_delivery_unlocked` | threshold, subtotal, currency, surface (cart\|checkout), zone (cart only) | phase 4 + phase 5 | checkout/page.tsx (once per visit), FreeDeliveryNudge.tsx (once per crossing, on the cart) |
| `cart_addon_added` | product_name, price, personalised, currency | phase 5 | CartAddonTray.tsx — an extra taken from the basket's tray |
| `personalisation_entered` | product_name, type (`handwritten_note`), length | phase 5 | PersonalisationField.tsx — once per line, on the first save carrying text |
| `payment_method_selected` | method (`card` \| `cod`), delivery_method, total, currency | phase 4 | checkout/page.tsx |
| `checkout_step_complete` | step (always 1), delivery_method | phase 1 | checkout/page.tsx |
| `payment_retry` | order_number, provider (the *gateway* the previous attempt used) | phase 4 | checkout/page.tsx (paying for an order that already exists) |
| `order_completed` | order_number, total, subtotal, delivery_fee, low_order_fee, discount, promo_code, payment_provider, delivery_method, item_count, is_guest, currency | existing + phase 4 | checkout/confirmation/page.tsx |
| `user_signup` | method: 'email', surface | phase 2 | signup/page.tsx, checkout/confirmation/CreateAccountNudge.tsx |
| `user_login` | method: 'email' | phase 2 | login/page.tsx |
| `logout` | — | phase 4 | auth-context.tsx (so the sidebar and mobile menu cannot disagree) |
| `password_reset_requested` | — | phase 4 | forgot-password/page.tsx |
| `password_reset_completed` | — | phase 4 | reset-password/page.tsx |
| `phone_verify_started` | surface | phase 4 | PhoneVerify.tsx |
| `phone_verify_sent` | surface | phase 4 | PhoneVerify.tsx |
| `phone_verify_resent` | surface | phase 4 | PhoneVerify.tsx ("Resend code") |
| `phone_verify_succeeded` | surface | phase 4 | PhoneVerify.tsx |
| `order_tracked` | order_number, status, delivery_method | phase 3 | track/page.tsx |
| `contact_click` | channel (whatsapp\|email\|instagram\|map\|phone), surface | phase 3 + phase 4 | ContactLink.tsx — contact page, **footer**, **FAQ CTA** |
| `faq_opened` | question, position | phase 4 | FaqAccordion.tsx (opening only, never closing) |
| `locale_changed` | from, to | phase 3 | LanguageSwitcher.tsx |

### Unhappy path

Everything below was invisible before phase 4. A customer met a red toast and
the dashboard recorded a quiet day.

| Event | Payload fields | Phase | Fired from |
|---|---|---|---|
| `search_no_results` | query, category | phase 4 | SearchTracker.tsx — the shop's most actionable list: a product to stock or a synonym to index |
| `product_unavailable` | product_name, surface, reason: out_of_stock | phase 4 | ProductDetailATC.tsx — demand arriving at a page that cannot sell |
| `add_to_cart_failed` | product_name, surface, reason | phase 4 | ProductDetailATC.tsx, AddToCartControl.tsx, ModifierModal.tsx |
| `cart_action_failed` | action (add\|update\|remove), reason, surface | phase 4 + phase 5 | cart/page.tsx, AddToCartControl.tsx, CartAddonTray.tsx |
| `cart_empty` | — | phase 4 | cart/page.tsx |
| `promo_failed` | code, reason (server's words, ≤60 chars), surface, subtotal, from_tray | phase 3 + phase 4 | cart/page.tsx, PromoCodeStep.tsx |
| `checkout_load_failed` | — | phase 4 | checkout/page.tsx — the basket could not be read, so no form ever rendered |
| `checkout_cart_empty` | — | phase 4 | checkout/page.tsx (submit with nothing in the basket) |
| `checkout_error` | step (always 1), field, fields, error_count, delivery_method | phase 1 + phase 4 | checkout/page.tsx — `handleSubmit` validation, **and** a Place Order gate deflection (`error_count: 1`), which is now where most of them come from: the button names the missing step before it can be pressed |
| `delivery_unserviceable` | subtotal, item_count, currency | phase 4 | checkout/page.tsx — **a basket we cannot deliver.** Once per visit |
| `low_order_fee_applied` | fee, subtotal, currency | phase 4 | checkout/page.tsx (once per visit) |
| `geolocation_denied` | surface | phase 4 | LocationPicker.tsx — why the one-tap shortcut looks unused |
| `address_save_failed` | surface, reason | phase 4 | AddressModal.tsx, account/addresses/page.tsx — the order still goes through, but the address stops being remembered |
| `order_create_failed` | reason, status, delivery_method, total, has_promo, currency | phase 4 | checkout/page.tsx — **our API refused the order.** Never reached a gateway |
| `payment_cancelled` | order_number, provider (gateway) | phase 4 | checkout/page.tsx — came back from the gateway unpaid. An order exists and is not paid for |
| `payment_failed` | order_number, error_message, reason, provider (gateway: `stripe` \| `ziina`; the chosen method on a failure before an order existed), total, stage (create_order\|create_session), currency | phase 1 + phase 4 | checkout/page.tsx (handleSubmit catch) |
| `login_failed` | reason, status | phase 4 | login/page.tsx |
| `signup_failed` | reason, status | phase 4 | signup/page.tsx |
| `password_reset_failed` | stage (request\|reset), reason | phase 4 | forgot-password/page.tsx, reset-password/page.tsx |
| `phone_verify_send_failed` | surface, reason (rate_limited\|unavailable) | phase 4 | PhoneVerify.tsx — an SMS that never arrived |
| `phone_verify_failed` | surface | phase 4 | PhoneVerify.tsx — a code that did not match |
| `order_track_failed` | reason | phase 4 | track/page.tsx — a customer who cannot see their order, whose next step is WhatsApp |
| `api_error` | status, endpoint (normalised), method | phase 4 | **`lib/api-client.ts` (formerly `lib/api.ts`) — one hook covering every endpoint, present and future.** `status: 0` means the request never arrived. **401 is never reported** — see below |
| `app_error` | digest, path | phase 4 | app/error.tsx — the "Something Went Wrong" screen. Sentry has the stack; this has the journey |
| `page_not_found` | path, referrer | phase 4 | NotFoundTracker.tsx — the referrer is what makes it actionable |

**On `api_error` and 401.** A 401 is never recorded. `/auth/me` runs on every
page load to find out whether anybody is signed in, and for a shopper who is not
— most of this site's traffic — the honest answer is 401. Reporting it made
`api_error` fire once per anonymous page view: the loudest event on the site,
saying only "somebody visited while logged out", burying the 500s it exists to
surface and spending the event quota on it. That was live for roughly twenty
minutes on 8 August 2026 before it was caught. A session that dies mid-journey is
still visible, because whatever the customer was doing when it died fails its own
way and has its own event. `apps/web/lib/api.test.ts` fails if this comes back.

**On `api_error` cardinality.** `normalisePath()` collapses identifiers before
sending: `/orders/MM-20260808-0042` → `/orders/:orderNumber`,
`/products/salted-caramel-brownie` → `/products/:slug`, UUIDs and numeric ids
→ `/:id`. Without it the property would become a list of every order number that
ever failed — thousands of rows with a count of one each, and no way to see
which endpoint is actually broken. There are tests for this in
`apps/web/lib/analytics.test.ts`; add a case there before adding a route shape.

---

## Goals

Navigate to: **Umami dashboard → [Website] → Goals → Create goal**

Each is an **Event** goal whose value is the event name.

### Happy path

| Goal name | Event | What it answers |
|---|---|---|
| Purchase Completed | `order_completed` | the number everything else is measured against |
| Checkout Reached | `view_checkout` | arrivals at the form, guest and account alike |
| Checkout Started | `begin_checkout` | left the basket towards the checkout |
| Cart Viewed | `view_cart` | baskets seen, with what was in them |
| Add to Cart | `add_to_cart` | |
| Product Viewed | `view_product` | |
| Search Performed | `search` | |
| Promo Applied | `promo_applied` | |
| Free Delivery Unlocked | `free_delivery_unlocked` | how often the threshold is actually earned — filter on `surface` to see whether the cart or the checkout is where baskets grow |
| Cart Add-on Added | `cart_addon_added` | whether the basket's tray earns its place on the page |
| Promotion Clicked | `select_promotion` | whether the hero and the banners do anything |
| Phone Verified | `phone_verify_succeeded` | the step that gates the new-customer coupon |
| Account Registered | `user_signup` | |

### Unhappy path — the half that was missing

These are the ones to watch. A rise in any of them is money leaving.

| Goal name | Event | Why it matters |
|---|---|---|
| Payment Cancelled | `payment_cancelled` | an order exists and nobody paid for it |
| Payment Failed | `payment_failed` | the gateway refused after the order was written |
| Order Rejected | `order_create_failed` | **our own API** refused it — a coupon rule, a zone, a sold-out line |
| Undeliverable Address | `delivery_unserviceable` | a full basket at an address we do not serve |
| Checkout Validation Error | `checkout_error` | the form refused to submit |
| Promo Rejected | `promo_failed` | a code somebody was given and could not use |
| Empty Search | `search_no_results` | a product to stock, or a word to teach the index |
| Add to Cart Failed | `add_to_cart_failed` | |
| API Error | `api_error` | any endpoint returning not-ok, anywhere on the site |
| App Crash | `app_error` | the "Something Went Wrong" screen |
| Page Not Found | `page_not_found` | |
| Phone Verification Failed | `phone_verify_failed` | |

---

## Funnels

Navigate to: **Umami dashboard → [Website] → Funnels → Create funnel**

All nine use a **60-minute window**.

### The rule this site's funnels kept getting wrong

**`view_product` is not a step in this shop's journey.** Every listing tile
carries its own add-to-cart control (`AddToCartControl.tsx`), so a customer can
fill a basket and buy without ever opening a product page. Any funnel that
*requires* `view_product` therefore discards most of the traffic that converts.

This was not theoretical. Measured over the 30 days to 8 August 2026, the Main
Purchase Funnel read:

| With `view_product` required | Without it |
|---|---|
| `/*` 686 → view_product 114 → add_to_cart **7** → begin_checkout **3** → order_completed **0** | `/*` 686 → add_to_cart **24** → begin_checkout **11** → order_completed **3** |

The funnel had been reporting **zero conversions for a month** while three
orders went through it. Widening the window to a full day did not help — it was
never a timing problem, it was the wrong step. Put `view_product` in a funnel
only when the question really is about the product page.

The second rule: **a step that can fire out of order will silently drop
conversions.** `promo_applied` fires from the basket *and* from the checkout, so
the Promo Code Funnel's old `… → promo_applied → begin_checkout → …` ordering
lost anyone who typed their code on the checkout page. Measured, that was one
purchase in three.

### 1. Main Purchase Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Any landing | URL | `/*` |
| 2. Add to cart | Event | `add_to_cart` |
| 3. Checkout started | Event | `begin_checkout` |
| 4. Purchase | Event | `order_completed` |

Fixed 8 August 2026 — `view_product` removed as step 2. See above.

### 2. Purchase Funnel (full)

The same journey with the two stages that were unmeasurable before phase 4.
Reads zero until phase 4 is deployed, then supersedes funnel 1.

| Step | Match type | Value |
|---|---|---|
| 1. Add to cart | Event | `add_to_cart` |
| 2. Cart viewed | Event | `view_cart` |
| 3. Checkout reached | Event | `view_checkout` |
| 4. Purchase | Event | `order_completed` |

### 3. Promo Code Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Add to cart | Event | `add_to_cart` |
| 2. Promo applied | Event | `promo_applied` |
| 3. Purchase | Event | `order_completed` |

Fixed 8 August 2026 — `begin_checkout` removed from between steps 2 and 3.

### 4. Search-to-Purchase Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Search | Event | `search` |
| 2. Add to cart | Event | `add_to_cart` |
| 3. Purchase | Event | `order_completed` |

Fixed 8 August 2026 — `view_product` removed.

### 5. Coupon Tray Funnel

Does the new-customer tray earn its space? The impression is step one, so the
apply rate finally has a denominator.

| Step | Match type | Value |
|---|---|---|
| 1. Tray seen | Event | `coupon_tray_shown` |
| 2. Code applied | Event | `promo_applied` |
| 3. Purchase | Event | `order_completed` |

### 5b. Coupon Banner Funnel

The same question one step earlier. The tray is seen by somebody who has
already filled a basket; the homepage strip is seen before anyone has decided
to buy, which is the whole reason it exists — so whether announcing the offer
earlier reaches customers the tray never did has to be readable on its own,
not unpicked out of the tray's numbers.

| Step | Match type | Value |
|---|---|---|
| 1. Banner seen | Event | `coupon_banner_shown` |
| 2. Banner clicked | Event | `coupon_banner_clicked` |
| 3. Code applied | Event | `promo_applied` |
| 4. Purchase | Event | `order_completed` |

### 6. Delivery Address Funnel

Where a delivery order dies between "I want this" and a priced address.

| Step | Match type | Value |
|---|---|---|
| 1. Checkout reached | Event | `view_checkout` |
| 2. Pin set | Event | `location_pin_set` |
| 3. Quote returned | Event | `delivery_quote` |
| 4. Purchase | Event | `order_completed` |

### 7. Payment Recovery Funnel

How many of the orders that come back unpaid are ever rescued.

| Step | Match type | Value |
|---|---|---|
| 1. Payment cancelled | Event | `payment_cancelled` |
| 2. Retried | Event | `payment_retry` |
| 3. Purchase | Event | `order_completed` |

### 8. Merchandising Funnel

Whether the homepage's most expensive space sells anything.

| Step | Match type | Value |
|---|---|---|
| 1. Promotion clicked | Event | `select_promotion` |
| 2. Add to cart | Event | `add_to_cart` |
| 3. Purchase | Event | `order_completed` |

### 9. Phone Verification Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Started | Event | `phone_verify_started` |
| 2. Code sent | Event | `phone_verify_sent` |
| 3. Verified | Event | `phone_verify_succeeded` |

---

## Journeys

Umami generates these itself from views and events — there is nothing to
configure and nothing to keep in sync. Audited 8 August 2026: working, left
alone.

---

## Editing goals and funnels without the UI

The dashboard's own API, which is quicker and less error-prone than the dialogs
when changing several at once. **The same-origin `cloud.umami.is/api/reports`
is not it** — Next serves the app shell for a POST there, which looks like a
`200` and silently does nothing. The real host is the regional gateway:

| | |
|---|---|
| List | `GET https://cloud.umami.is/api/websites/{websiteId}/reports?pageSize=200` (cookie auth) |
| Create | `POST https://gateway-us.umami.is/api/reports` |
| Update | `POST https://gateway-us.umami.is/api/reports/{reportId}` (send the whole record back) |
| Run a funnel | `POST https://gateway-us.umami.is/api/reports/funnel` |

The gateway calls need `authorization: Bearer …`, which the dashboard holds in
memory rather than a cookie. Read it off a live request from the browser console
rather than storing it anywhere.

Goal body: `{name, type: 'goal', websiteId, parameters: {type: 'event', value: '<event_name>'}}`.
Funnel body: `{name, type: 'funnel', websiteId, parameters: {steps: [{type: 'event'|'path', value, filters: []}], window: 60}}`.

Before changing a funnel's steps, **run both shapes and compare** — that is how
the `view_product` fault above was found, and a funnel that looks reasonable can
still be reporting zero.

---

## Troubleshooting — "the events aren't coming through"

Work down this list. Each step separates a different failure, and the ones near
the top are the ones that have actually happened here.

### 1. Is the tracker on the page at all?

```bash
curl -s https://meltingmomentscakes.com/en | grep -o 'data-website-id[^,]*'
```

Expect the website ID. Nothing means `NEXT_PUBLIC_UMAMI_WEBSITE_ID` is missing
from the storefront's build environment — it is a `NEXT_PUBLIC_` variable, so it
is inlined at build time and a value added afterwards changes nothing until the
next deploy.

### 2. Does the proxy still reach Umami?

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://meltingmomentscakes.com/vague/v.js
curl -s -w '\n%{http_code}\n' -X POST https://meltingmomentscakes.com/vague/api/send \
  -H 'Content-Type: application/json' \
  -H 'x-umami-website-id: 00000000-0000-0000-0000-000000000000' \
  --data '{"type":"event","payload":{"website":"00000000-0000-0000-0000-000000000000","hostname":"meltingmomentscakes.com","url":"/","name":"probe"}}'
```

Expect `200` for the script and Umami's own `400` for the send — a deliberately
invalid website ID, so it proves the path without recording anything.

**A `307` on the second command is a fault.** It means `/vague/` has fallen back
under the locale rule in `apps/web/proxy.ts` and every event is being sent
twice. That was the state of production until 5 August 2026.

### 3. Is anything blocking it in the browser?

Both the script and the send are same-origin — the script rewritten in
`next.config.ts`, the send handled by `apps/web/app/vague/api/send/route.ts` — which
is what keeps ordinary blocklists off them. If either is ever changed to point
straight at `cloud.umami.is`, expect a large and uneven share of events to
vanish, because that hostname is on the common privacy lists and the shop's
traffic is overwhelmingly mobile.

Neither path names the product. When this was checked on 6 August 2026, nothing
in EasyPrivacy, uBlock Origin's privacy list or AdGuard's tracking list matched
the first-party `/umami/...` paths these replaced — every umami rule in them is a
host rule (`||umami.is^$third-party`, `||umami.`) or `/umami.js`. So the naming
is insurance against a list that starts matching the obvious string, not a
repair. Keep it that way: the tracker appends `/api/send` to `data-host-url`
itself, so only the prefix is ours, and it should stay something no generic rule
can pattern-match.

### 3b. Is the country wrong?

The proxy is why. A proxy opens its own connection to Umami, so without help
Umami sees the relay, not the visitor — and because Umami Cloud is itself behind
Cloudflare, the `cf-connecting-ip` / `cf-ipcountry` pair stamped on that
connection **outranks `X-Forwarded-For`**. Forwarding the standard proxy header
alone fixes nothing.

`apps/web/app/vague/api/send/route.ts` sends the two things that do outrank it:

| Channel | Why it works |
|---|---|
| `payload.ip` in the event body | Umami prefers it over every header, and having it, skips header lookup entirely — Cloudflare's included. Cannot be overwritten upstream. |
| `x-umami-client-ip`, `x-umami-client-country`, `-region`, `-city` | Read before the Cloudflare headers in Umami Cloud. Copied straight from Vercel's `x-vercel-ip-*`, which already resolved the visitor at the edge. |

If country goes blank or wrong again, check in this order: the route handler is
still a route handler and not a rewrite (a rewrite loses this silently); the
request still arrives with `x-vercel-ip-country` set; and the site is still
served by Vercel — the `x-vercel-ip-*` half of this is platform-specific and a
move off Vercel leaves only `payload.ip`, which is still correct but relies on
Umami's own GeoIP database.

### 4. Can the dashboard read back?

Open **Admin → Analytics**. A banner above the traffic cards carries Umami's own
reason when the read fails. `401`/`403` there means either `UMAMI_API_KEY` is
wrong or **the Umami Cloud plan on the account does not include API access** —
the read API is not part of the free tier, and the storefront can be recording
events perfectly while this panel stays empty.

### 5. Only then suspect the dashboard config

Goals and funnels below are hand-made in Umami and are not needed for events to
be *recorded*. If **Events** shows the counts but a funnel shows nothing, the
funnel is what is wrong, not the tracking.

### Known limits, so they are not rediscovered as bugs

- Events fired before the tracker finishes loading are queued and retried for
  30 seconds (`apps/web/lib/analytics.ts`). Beyond that they are dropped.
- **Country, region and city recorded before 6 August 2026 are the proxy's, not
  the visitor's.** Every event was relayed by the Vercel edge, and Umami read
  Cloudflare's view of that relay — so the dashboard reported wherever the edge
  PoP sat (`bom1` reads as India, `sin1` as Singapore) and the UAE never
  appeared at all. Treat any geography older than that date as a map of Vercel's
  network, and do not compare it with what the dashboard shows now.
- Network / ISP is still the proxy's and is not meaningful. Everything else —
  events, pages, referrers, browser, OS, device — was always correct.
- `order_completed` fires whenever the confirmation page is opened, including a
  refresh or a revisit of the link. Treat it as an upper bound; the order table
  is the source of truth for how many orders were placed.
- **`view_cart`, `view_checkout`, `delivery_unserviceable`,
  `free_delivery_unlocked` and `low_order_fee_applied` fire once per page visit,
  not once per change.** They are guarded by refs, because the basket is
  refetched on every mutation and without the guard removing one brownie would
  count as arriving at the basket again. A hard refresh is a new visit and does
  count again.
- **`api_error` and `page_not_found` are client-side only.** Anything fetched on
  the server — listing pages, `generateMetadata`, the sitemap — goes through
  `RSC_API_BASE` and a plain `fetch`, not through `lib/api.ts`, so a server-side
  failure is in Sentry and the platform logs and not here.
- **`select_promotion` and `select_item` are click events on links.** A middle
  click, a long-press "open in new tab" and a right-click do not fire them, and
  a customer who navigates away before the request leaves loses it — the send is
  fire-and-forget with no `keepalive`. Treat both as a floor.
- `modifier_selected` deliberately ignores the option preselected for a group
  that only allows one answer, and ignores a press that hits the group's
  ceiling. It counts decisions, not renders.

---

## Changelog

| Date | Change |
|---|---|
| 2026-08-15 | **No event added, removed or renamed — but `checkout_error` means something different now, and will rise.** The Place Order button became a state machine: instead of accepting a press and then refusing it, the button names the next missing step (`Set address`, `Enter email address`, `Choose a store`, …) and pressing it takes the customer there. A deflection fires `checkout_error` with `error_count: 1` and the offending `field`, so **most of this event's volume is now pre-emptive rather than a failed submit**, and the count will go up while the experience gets better — do not read the rise as a regression. `handleSubmit`'s validation still fires it the old way as the backstop, so the two are mixed; `error_count > 1` is only ever a real submit failure. Email also became mandatory at this deploy, so expect a new `field: email` population that did not exist before (it was an optional field until now). |
| 2026-08-10 | **No event added, removed or renamed, and no Umami history is affected** — but every event now goes to a second tool. Microsoft Clarity was added to the storefront (`docs/microsoft-clarity-setup.md`), fed from inside `track()` rather than from the call sites, so all 66 events reach it under the same names and any event added here in future reaches it with no extra work. Two changes to shared code came with it and are worth knowing when reading this file: the pre-load queue moved out of `analytics.ts` into `lib/deferred-dispatch.ts` — same 250ms poll, same 30s ceiling, same ordering guarantee, now with a 500-item cap so a session behind a blocker cannot grow it without bound — and `clean()` now runs once, before either tool is called, so Clarity can never see a property Umami was not given. Nothing to change in the Umami dashboard. |
| 2026-08-10 | **No event added, removed or renamed — but `phone_verify_succeeded` means something wider now.** `PhoneVerify` asks `/auth/phone-verified` before buying an SMS, and a number already proved inside `PHONE_VERIFICATION_TTL_SECONDS` short-circuits straight to success. So **`phone_verify_succeeded` can now fire with no preceding `phone_verify_sent`**, and funnel 3's step 2→3 will read above 100% for returning customers. Read `phone_verify_sent` as *messages we paid for* and `phone_verify_succeeded` as *numbers proved*; they are no longer the same denominator. A 60s resend cooldown and a 3-send ceiling per panel were added at the same time, so `sent` may fall slightly; measured volume was three sends in twelve days, so do not expect a visible step. A sustained `sent` with a flat `succeeded` is now the signal worth alerting on: that is either SMS not arriving, or someone else spending the budget. |
| 2026-08-09 | **Two events added: `cart_addon_added`, `personalisation_entered`** (`CartAddonTray.tsx`, `PersonalisationField.tsx` — the basket's new add-on tray and the handwritten-note field). **Two existing events gained fields, neither renamed:** `free_delivery_unlocked` now carries `surface` (`cart`\|`checkout`) and, on the cart only, `zone`; `cart_action_failed`'s `action` gained `add`. Nothing removed. No funnel changes. Context: the free-delivery threshold was previously only ever said at checkout, where the basket is already decided — it now also appears on the cart, so expect `free_delivery_unlocked` volume to roughly double and **history before this date to be checkout-only**; filter on `surface` before comparing across the boundary. `zone` is absent on checkout events by design, because the checkout reads its threshold from a settled quote rather than from a named area. New goal **Cart Add-on Added**. |
| 2026-08-09 | **Two events added: `coupon_banner_shown`, `coupon_banner_clicked`** (`PromoBanner.tsx`, the new homepage strip). Nothing removed or renamed. New funnel **5b — Coupon Banner**, kept separate from funnel 5 because the strip is seen before a basket exists and the tray after one, so merging them would hide whether announcing the offer earlier reaches anyone new. No goal changes. Context: the new-customer coupon was previously unreachable in practice — the basket tray validated without a phone, so a coupon requiring one always refused, and the only verification UI a guest could reach sat behind a collapsed "add promo or note" panel. The code now applies at the basket and the phone gate is enforced only on delivery orders at `create_order`, so `promo_applied` fires in cases that previously fired `promo_failed` with reason `Verify your phone number…` — expect that reason to drop to near zero and `promo_applied` on surface `cart` to rise. |
| 2026-04-18 | Initial setup — 15 events, 7 goals, 3 funnels across 3 phases |
| 2026-04-18 | Fix Main Purchase Funnel step 1: `/` → `/*` to match locale-prefixed homepages (`/en`, `/ar`) |
| 2026-06-05 | Queue custom events briefly when the Umami script has not loaded yet; no event names or payload fields changed |
| 2026-08-02 | Checkout collapsed from 3 steps to 2 (delivery method moved into step 1). `checkout_step_complete` and `checkout_error` now emit `step` 1\|2 instead of 1\|2\|3; step 1 now also carries `delivery_method`. No events added or removed. |
| 2026-08-02 | Checkout collapsed again from 2 steps to a single page. `checkout_step_complete` now fires once, on submit, always with `step: 1` and a `delivery_method`; `checkout_error` likewise always reports `step: 1`. No events added or removed — but the Main Purchase Funnel's step 4→5 is now a single page view, so treat any step-2 history before this date as a different shape. |
| 2026-08-06 | Geography fixed. The send is a route handler (edge runtime) instead of a `next.config.ts` rewrite, and now forwards the visitor's location to Umami as `payload.ip` plus the `x-umami-client-{ip,country,region,city}` headers — the two channels that outrank the Cloudflare headers Umami Cloud sits behind. Pre-existing geography is the proxy's and is not comparable; see Known limits. |
| 2026-08-06 | Analytics paths renamed: `/umami/script.js` → `/vague/v.js`, `/umami/api/send` → `/vague/api/send` (`data-host-url` is now `/vague`). No event added, removed or renamed, and the website ID is unchanged, so no history is affected. The old paths matched no blocklist rule — this is insurance against one appearing, and the reasoning is in `apps/web/app/vague/api/send/route.ts`. |
| 2026-08-06 | `NEXT_PUBLIC_UMAMI_URL` is no longer read — the script path is hard-coded in `app/layout.tsx` alongside `data-host-url`. The rename above shipped while Vercel still held `/umami/script.js` in that variable, so the tag pointed at a 404 and the tracker stopped loading for roughly ten minutes; `data-host-url` had moved with the code, so nothing in the markup looked wrong. Only the website ID belongs in the environment. Delete the variable from any project that still sets it. |
| 2026-08-05 | Audit. No events added, removed or renamed. Fixed the **Fired from** column, which had drifted for `add_to_cart`, `remove_from_cart`, `user_signup` and `select_delivery_method`. Three delivery faults fixed in code: `/umami/api/send` was being locale-redirected so every event was sent twice; the pre-load queue gave up after 3s and now waits 30s; the basket sent the browser to `/checkout` without a locale, discarding `begin_checkout` across the redirect. The admin dashboard now reads `metrics?type=event` and reports Umami's refusals instead of showing zeros. Added the Troubleshooting section above. |
| 2026-08-08 | **Card payments became gateway-agnostic — `provider` changed meaning, `method` changed values.** No events were added or removed. What moved is what the properties say. `payment_method_selected.method` now reports `card` where it reported `stripe`: the customer picks a *method*, and which processor settles it (Stripe or Ziina) is chosen server-side from the `payment_gateways` table so a processor outage can be answered by an admin toggle instead of a deploy. Conversely `provider` on `payment_failed`, `payment_retry` and `payment_cancelled` now carries the **gateway** rather than the method — `stripe` or `ziina` — which is the value worth alerting on, because `card` only says a payment broke while `stripe` says which processor broke it. `order_completed.payment_provider` was already the gateway and is unchanged in meaning, but will start showing `ziina` if the estate is ever switched over. **Dashboard action:** any saved report or funnel that filters `payment_method_selected` on `method = 'stripe'` must be changed to `method = 'card'` — it will read zero otherwise. Reports segmenting failures by `provider` need no change and become more useful. Production remains Stripe-only; Ziina ships inactive and unconfigured. |
| 2026-08-08 | **Phase 4 — 47 events added, 9 enriched, 19 → 66 total.** The whole unhappy path is now recorded: `api_error` (one hook in `lib/api.ts` covering every endpoint), `order_create_failed`, `payment_cancelled`, `delivery_unserviceable`, `search_no_results`, `add_to_cart_failed`, `login_failed`, `signup_failed`, `phone_verify_*`, `app_error`, `page_not_found` and the rest. New happy-path coverage: `view_cart`, `view_checkout`, `delivery_quote`, `select_promotion`, `select_item`, `modifier_selected`, `coupon_tray_shown`, `location_pin_set`, `sort_products`, `faq_opened`. Enriched: `order_completed` now carries the total broken into subtotal / delivery fee / low-order fee / discount / promo code / is_guest; `add_to_cart` carries `value` and `surface`; `promo_applied` and `promo_failed` carry `surface` and `subtotal`; `checkout_error` carries every failing field; `payment_failed` carries `stage` and `provider`; `search` carries `has_results`. Conventions introduced: `currency: 'AED'` on money, a stable `reason` slug on failures, `surface` everywhere, and empty properties dropped before send. Goals rebuilt: 24 in the dashboard (12 happy + 12 unhappy), up from 7. Also fixed in passing: the footer and FAQ WhatsApp buttons were plain anchors and had never been tracked. |
| 2026-08-08 | **Funnel audit — two of the three existing funnels were quietly wrong.** The **Main Purchase Funnel** required `view_product` as step 2, but every listing tile can add to the basket without opening a product page, so the funnel discarded 71% of add-to-carts and **every purchase**: it had reported `order_completed = 0` for thirty days while three orders went through it. Removing that step gives `/* 686 → add_to_cart 24 → begin_checkout 11 → order_completed 3`. The **Promo Code Funnel** had `begin_checkout` between `promo_applied` and `order_completed`, but `promo_applied` fires from the checkout page too — so anyone who typed their code there broke the ordering and fell out despite converting; measured, one purchase in three. `begin_checkout` removed. **Search-to-Purchase** had the same `view_product` fault and was fixed the same way. Six new funnels added (purchase full, coupon tray, delivery address, payment recovery, merchandising, phone verification) — nine in total. This file's previous description of the funnels did not match the dashboard at all (it described five URL steps that were not there), which is how the `view_product` fault survived; the tables above are now transcribed from the live configuration. Journeys audited — Umami generates those itself, nothing to configure. Pipeline verified live end-to-end: `search` and `view_product` fired from meltingmomentscakes.com and arrived in Umami within seconds, geo-attributed to AE/Sharjah. |
| 2026-08-08 | `api_error` no longer reports 401. `/auth/me` runs on every page load and answers 401 for anyone not signed in, so the event was firing once per anonymous page view — the loudest thing on the dashboard, and meaningless. Caught within twenty minutes of the phase 4 deploy by watching what actually arrived rather than trusting the green tick. Four regression tests added in `apps/web/lib/api.test.ts`, including one that fails if 401 reporting returns. |
| 2026-08-15 | **Fired-from correction, no event change.** `delivery_quote` moved from `checkout/page.tsx` to `checkout/hooks/useOrderPreview.ts` and now fires on each settled `POST /orders/preview` rather than each `POST /delivery/quote`. The checkout stopped deriving its own money — the grand total, the small-basket fee and the VAT line were all computed in the browser — and now renders one server-priced response that carries the delivery quote inside it, so the two calls became one. Every property is unchanged and carries the same meaning; `subtotal` is still the discounted basket the fee was priced against. No goal or funnel needs editing. |
