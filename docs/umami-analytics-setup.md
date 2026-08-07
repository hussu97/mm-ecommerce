# Umami Analytics Setup — Melting Moments

This file tracks every manual configuration that must exist in the Umami dashboard.
Update it whenever `apps/web/lib/analytics.ts` changes.

---

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
| `promo_applied` | code, discount, surface, subtotal, from_tray, currency | existing + phase 4 | cart/page.tsx, PromoCodeStep.tsx |
| `begin_checkout` | item_count, subtotal, has_promo, currency | existing + phase 4 | cart/page.tsx |
| `view_checkout` | item_count, subtotal, is_guest, has_saved_address, currency | phase 4 | checkout/page.tsx (once per visit) |
| `saved_address_selected` | surface | phase 4 | AddressModal.tsx |
| `address_saved` | surface, has_pin, is_new | phase 4 | AddressModal.tsx, account/addresses/page.tsx |
| `address_deleted` | surface | phase 4 | AddressModal.tsx, account/addresses/page.tsx |
| `location_pin_set` | method (autocomplete\|map_tap\|drag\|current_location), surface | phase 4 | LocationPicker.tsx |
| `select_delivery_method` | method, fee, currency | phase 3 | checkout/page.tsx (the delivery/pickup toggle) |
| `pickup_branch_selected` | branch_name | phase 4 | checkout/page.tsx |
| `delivery_quote` | serviceable, delivery_fee, base_fee, free_applied, free_available, free_threshold, subtotal, currency | phase 4 | checkout/page.tsx (each settled quote with a pin behind it) |
| `free_delivery_unlocked` | threshold, subtotal, currency | phase 4 | checkout/page.tsx (once per visit) |
| `payment_method_selected` | method, delivery_method, total, currency | phase 4 | checkout/page.tsx |
| `checkout_step_complete` | step (always 1), delivery_method | phase 1 | checkout/page.tsx |
| `payment_retry` | order_number, provider | phase 4 | checkout/page.tsx (paying for an order that already exists) |
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
| `cart_action_failed` | action (update\|remove), reason, surface | phase 4 | cart/page.tsx, AddToCartControl.tsx |
| `cart_empty` | — | phase 4 | cart/page.tsx |
| `promo_failed` | code, reason (server's words, ≤60 chars), surface, subtotal, from_tray | phase 3 + phase 4 | cart/page.tsx, PromoCodeStep.tsx |
| `checkout_load_failed` | — | phase 4 | checkout/page.tsx — the basket could not be read, so no form ever rendered |
| `checkout_cart_empty` | — | phase 4 | checkout/page.tsx (submit with nothing in the basket) |
| `checkout_error` | step (always 1), field, fields, error_count, delivery_method | phase 1 + phase 4 | checkout/page.tsx (handleSubmit validation) |
| `delivery_unserviceable` | subtotal, item_count, currency | phase 4 | checkout/page.tsx — **a basket we cannot deliver.** Once per visit |
| `low_order_fee_applied` | fee, subtotal, currency | phase 4 | checkout/page.tsx (once per visit) |
| `geolocation_denied` | surface | phase 4 | LocationPicker.tsx — why the one-tap shortcut looks unused |
| `address_save_failed` | surface, reason | phase 4 | AddressModal.tsx, account/addresses/page.tsx — the order still goes through, but the address stops being remembered |
| `order_create_failed` | reason, status, delivery_method, total, has_promo, currency | phase 4 | checkout/page.tsx — **our API refused the order.** Never reached a gateway |
| `payment_cancelled` | order_number, provider | phase 4 | checkout/page.tsx — came back from the gateway unpaid. An order exists and is not paid for |
| `payment_failed` | order_number, error_message, reason, provider, total, stage (create_order\|create_session), currency | phase 1 + phase 4 | checkout/page.tsx (handleSubmit catch) |
| `login_failed` | reason, status | phase 4 | login/page.tsx |
| `signup_failed` | reason, status | phase 4 | signup/page.tsx |
| `password_reset_failed` | stage (request\|reset), reason | phase 4 | forgot-password/page.tsx, reset-password/page.tsx |
| `phone_verify_send_failed` | surface, reason (rate_limited\|unavailable) | phase 4 | PhoneVerify.tsx — an SMS that never arrived |
| `phone_verify_failed` | surface | phase 4 | PhoneVerify.tsx — a code that did not match |
| `order_track_failed` | reason | phase 4 | track/page.tsx — a customer who cannot see their order, whose next step is WhatsApp |
| `api_error` | status, endpoint (normalised), method | phase 4 | **`lib/api.ts` — one hook covering every endpoint, present and future.** `status: 0` means the request never arrived |
| `app_error` | digest, path | phase 4 | app/error.tsx — the "Something Went Wrong" screen. Sentry has the stack; this has the journey |
| `page_not_found` | path, referrer | phase 4 | NotFoundTracker.tsx — the referrer is what makes it actionable |

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
| Free Delivery Unlocked | `free_delivery_unlocked` | how often the threshold is actually earned |
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

### 1. Main Purchase Funnel — events, not URLs

| Step | Match type | Value |
|---|---|---|
| 1. Product viewed | Event | `view_product` |
| 2. Add to cart | Event | `add_to_cart` |
| 3. Cart viewed | Event | `view_cart` |
| 4. Checkout reached | Event | `view_checkout` |
| 5. Purchase | Event | `order_completed` |

**This replaces the URL-based funnel that ran from April 2026 to August 2026.**
That one stepped through `/*`, `/*/*`, `/*/cart`, `/*/checkout` and
`/*/checkout/confirmation`, and every step of it counted the wrong thing: `/*/*`
matches any two-segment path rather than a product page, a refresh of the basket
counted as a second visit to it, an empty basket looked identical to a full one,
and a cancelled payment returning from the gateway counted as a fresh arrival at
the checkout. The events above carry the basket, so drop-off can be read against
basket size instead of guessed at.

The old URL funnel can be kept alongside under a different name if the history
matters, but do not compare its numbers with this one — they were never counting
the same thing.

### 2. Search-to-Purchase Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Search | Event | `search` |
| 2. Product viewed | Event | `view_product` |
| 3. Add to cart | Event | `add_to_cart` |
| 4. Purchase | Event | `order_completed` |

### 3. Promo Code Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Cart viewed | Event | `view_cart` |
| 2. Promo applied | Event | `promo_applied` |
| 3. Checkout reached | Event | `view_checkout` |
| 4. Purchase | Event | `order_completed` |

Step 1 was a URL (`/*/cart`) until August 2026 and is now the event, for the same
reason as funnel 1.

### 4. Coupon Tray Funnel

Does the new-customer tray earn its space? The impression is step one, so the
apply rate finally has a denominator.

| Step | Match type | Value |
|---|---|---|
| 1. Tray seen | Event | `coupon_tray_shown` |
| 2. Code applied | Event | `promo_applied` |
| 3. Purchase | Event | `order_completed` |

### 5. Delivery Address Funnel

Where a delivery order dies between "I want this" and a priced address.

| Step | Match type | Value |
|---|---|---|
| 1. Checkout reached | Event | `view_checkout` |
| 2. Pin set | Event | `location_pin_set` |
| 3. Quote returned | Event | `delivery_quote` |
| 4. Purchase | Event | `order_completed` |

### 6. Payment Recovery Funnel

How many of the orders that come back unpaid are ever rescued.

| Step | Match type | Value |
|---|---|---|
| 1. Payment cancelled | Event | `payment_cancelled` |
| 2. Retried | Event | `payment_retry` |
| 3. Purchase | Event | `order_completed` |

### 7. Merchandising Funnel

Whether the homepage's most expensive space sells anything.

| Step | Match type | Value |
|---|---|---|
| 1. Promotion clicked | Event | `select_promotion` |
| 2. Product viewed | Event | `view_product` |
| 3. Add to cart | Event | `add_to_cart` |
| 4. Purchase | Event | `order_completed` |

### 8. Phone Verification Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Started | Event | `phone_verify_started` |
| 2. Code sent | Event | `phone_verify_sent` |
| 3. Verified | Event | `phone_verify_succeeded` |

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
| 2026-04-18 | Initial setup — 15 events, 7 goals, 3 funnels across 3 phases |
| 2026-04-18 | Fix Main Purchase Funnel step 1: `/` → `/*` to match locale-prefixed homepages (`/en`, `/ar`) |
| 2026-06-05 | Queue custom events briefly when the Umami script has not loaded yet; no event names or payload fields changed |
| 2026-08-02 | Checkout collapsed from 3 steps to 2 (delivery method moved into step 1). `checkout_step_complete` and `checkout_error` now emit `step` 1\|2 instead of 1\|2\|3; step 1 now also carries `delivery_method`. No events added or removed. |
| 2026-08-02 | Checkout collapsed again from 2 steps to a single page. `checkout_step_complete` now fires once, on submit, always with `step: 1` and a `delivery_method`; `checkout_error` likewise always reports `step: 1`. No events added or removed — but the Main Purchase Funnel's step 4→5 is now a single page view, so treat any step-2 history before this date as a different shape. |
| 2026-08-06 | Geography fixed. The send is a route handler (edge runtime) instead of a `next.config.ts` rewrite, and now forwards the visitor's location to Umami as `payload.ip` plus the `x-umami-client-{ip,country,region,city}` headers — the two channels that outrank the Cloudflare headers Umami Cloud sits behind. Pre-existing geography is the proxy's and is not comparable; see Known limits. |
| 2026-08-06 | Analytics paths renamed: `/umami/script.js` → `/vague/v.js`, `/umami/api/send` → `/vague/api/send` (`data-host-url` is now `/vague`). No event added, removed or renamed, and the website ID is unchanged, so no history is affected. The old paths matched no blocklist rule — this is insurance against one appearing, and the reasoning is in `apps/web/app/vague/api/send/route.ts`. |
| 2026-08-06 | `NEXT_PUBLIC_UMAMI_URL` is no longer read — the script path is hard-coded in `app/layout.tsx` alongside `data-host-url`. The rename above shipped while Vercel still held `/umami/script.js` in that variable, so the tag pointed at a 404 and the tracker stopped loading for roughly ten minutes; `data-host-url` had moved with the code, so nothing in the markup looked wrong. Only the website ID belongs in the environment. Delete the variable from any project that still sets it. |
| 2026-08-05 | Audit. No events added, removed or renamed. Fixed the **Fired from** column, which had drifted for `add_to_cart`, `remove_from_cart`, `user_signup` and `select_delivery_method`. Three delivery faults fixed in code: `/umami/api/send` was being locale-redirected so every event was sent twice; the pre-load queue gave up after 3s and now waits 30s; the basket sent the browser to `/checkout` without a locale, discarding `begin_checkout` across the redirect. The admin dashboard now reads `metrics?type=event` and reports Umami's refusals instead of showing zeros. Added the Troubleshooting section above. |
| 2026-08-08 | **Phase 4 — 47 events added, 9 enriched, 19 → 66 total.** The whole unhappy path is now recorded: `api_error` (one hook in `lib/api.ts` covering every endpoint), `order_create_failed`, `payment_cancelled`, `delivery_unserviceable`, `search_no_results`, `add_to_cart_failed`, `login_failed`, `signup_failed`, `phone_verify_*`, `app_error`, `page_not_found` and the rest. New happy-path coverage: `view_cart`, `view_checkout`, `delivery_quote`, `select_promotion`, `select_item`, `modifier_selected`, `coupon_tray_shown`, `location_pin_set`, `sort_products`, `faq_opened`. Enriched: `order_completed` now carries the total broken into subtotal / delivery fee / low-order fee / discount / promo code / is_guest; `add_to_cart` carries `value` and `surface`; `promo_applied` and `promo_failed` carry `surface` and `subtotal`; `checkout_error` carries every failing field; `payment_failed` carries `stage` and `provider`; `search` carries `has_results`. Conventions introduced: `currency: 'AED'` on money, a stable `reason` slug on failures, `surface` everywhere, and empty properties dropped before send. Goals rebuilt (12 happy + 12 unhappy) and funnels rebuilt — the Main Purchase Funnel and the Promo Code Funnel are now event-based rather than URL-based, and five new funnels added (coupon tray, delivery address, payment recovery, merchandising, phone verification). Also fixed in passing: the footer and FAQ WhatsApp buttons were plain anchors and had never been tracked. |
