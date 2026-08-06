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

| Event | Payload fields | Phase | Fired from |
|---|---|---|---|
| `add_to_cart` | product_name, variant_name, price, quantity | existing | ProductDetailATC.tsx, AddToCartControl.tsx (every product tile), ModifierModal.tsx |
| `remove_from_cart` | product_name | existing | cart/page.tsx, AddToCartControl.tsx (stepping a tile to zero) |
| `begin_checkout` | item_count, subtotal | existing | cart/page.tsx |
| `promo_applied` | code, discount | existing + checkout | cart/page.tsx, PromoCodeStep.tsx |
| `order_completed` | order_number, total, payment_provider, delivery_method, item_count | existing | checkout/confirmation/page.tsx |
| `view_product` | product_name, category, price, has_modifiers | phase 1 | ProductDetailATC.tsx (on mount) |
| `checkout_step_complete` | step (always 1), delivery_method | phase 1 | checkout/page.tsx |
| `payment_failed` | order_number, error_message | phase 1 | checkout/page.tsx (handleSubmit catch) |
| `checkout_error` | step (always 1), field | phase 1 | checkout/page.tsx (handleSubmit validation) |
| `search` | query, result_count | phase 2 | SearchTracker.tsx (client wrapper in search/page.tsx) |
| `user_signup` | method: 'email' | phase 2 | signup/page.tsx, checkout/confirmation/CreateAccountNudge.tsx |
| `user_login` | method: 'email' | phase 2 | login/page.tsx |
| `view_category` | category_name, product_count | phase 2 | CategoryTracker.tsx (client wrapper in [category]/page.tsx) |
| `select_delivery_method` | method, fee | phase 3 | checkout/page.tsx (the delivery/pickup toggle) |
| `promo_failed` | code, reason | phase 3 | PromoCodeStep.tsx |
| `contact_click` | channel: whatsapp\|email\|instagram\|map | phase 3 | ContactLink.tsx (contact/page.tsx) |
| `locale_changed` | from, to | phase 3 | LanguageSwitcher.tsx |
| `order_tracked` | order_number, status | phase 3 | track/page.tsx |

---

## Goals

Navigate to: **Umami dashboard → [Website] → Goals → Create goal**

| Goal name | Type | Value |
|---|---|---|
| Purchase Completed | Event | `order_completed` |
| Checkout Started | Event | `begin_checkout` |
| Product Viewed | Event | `view_product` |
| Add to Cart | Event | `add_to_cart` |
| Account Registered | Event | `user_signup` |
| Search Performed | Event | `search` |
| Promo Applied | Event | `promo_applied` |

---

## Funnels

Navigate to: **Umami dashboard → [Website] → Funnels → Create funnel**

### 1. Main Purchase Funnel

| Step | Match type | Value |
|---|---|---|
| 1. Any landing | URL | `/*` |
| 2. Product page | URL | `/*/*` |
| 3. Cart | URL | `/*/cart` |
| 4. Checkout | URL | `/*/checkout` |
| 5. Confirmation | URL | `/*/checkout/confirmation` |

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
| 1. Cart | URL | `/*/cart` |
| 2. Promo applied | Event | `promo_applied` |
| 3. Checkout | URL | `/*/checkout` |
| 4. Purchase | Event | `order_completed` |

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
| 2026-08-06 | Analytics paths renamed: `/umami/script.js` → `/vague/v.js`, `/umami/api/send` → `/vague/api/send` (`data-host-url` is now `/vague`). No event added, removed or renamed, and the website ID is unchanged, so no history is affected. The old paths matched no blocklist rule — this is insurance against one appearing, and the reasoning is in `apps/web/app/vague/api/send/route.ts`. `NEXT_PUBLIC_UMAMI_URL`, if ever set, must be `/vague/v.js`. |
| 2026-08-05 | Audit. No events added, removed or renamed. Fixed the **Fired from** column, which had drifted for `add_to_cart`, `remove_from_cart`, `user_signup` and `select_delivery_method`. Three delivery faults fixed in code: `/umami/api/send` was being locale-redirected so every event was sent twice; the pre-load queue gave up after 3s and now waits 30s; the basket sent the browser to `/checkout` without a locale, discarding `begin_checkout` across the redirect. The admin dashboard now reads `metrics?type=event` and reports Umami's refusals instead of showing zeros. Added the Troubleshooting section above. |
