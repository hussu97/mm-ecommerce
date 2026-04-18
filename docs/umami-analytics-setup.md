# Umami Analytics Setup — Melting Moments

This file tracks every manual configuration that must exist in the Umami dashboard.
Update it whenever `apps/web/lib/analytics.ts` changes.

---

## Custom Events Reference

All events are fired via `window.umami.track(name, data)` from `apps/web/lib/analytics.ts`.
Umami records them automatically — no dashboard config needed for events to appear under the **Events** tab.

| Event | Payload fields | Phase | Fired from |
|---|---|---|---|
| `add_to_cart` | product_name, variant_name, price, quantity | existing | ProductDetailATC, ProductCard, ModifierModal |
| `remove_from_cart` | product_name | existing | cart/page.tsx |
| `begin_checkout` | item_count, subtotal | existing | cart/page.tsx |
| `promo_applied` | code, discount | existing + checkout | cart/page.tsx, PromoCodeStep.tsx |
| `order_completed` | order_number, total, payment_provider, delivery_method, item_count | existing | checkout/confirmation/page.tsx |
| `view_product` | product_name, category, price, has_modifiers | phase 1 | ProductDetailATC.tsx (on mount) |
| `checkout_step_complete` | step (1\|2\|3), delivery_method? | phase 1 | checkout/page.tsx |
| `payment_failed` | order_number, error_message | phase 1 | checkout/page.tsx (handleSubmit catch) |
| `checkout_error` | step (1\|2\|3), field | phase 1 | checkout/page.tsx (StepInformation.handleNext) |
| `search` | query, result_count | phase 2 | SearchTracker.tsx (client wrapper in search/page.tsx) |
| `user_signup` | method: 'email' | phase 2 | signup/page.tsx |
| `user_login` | method: 'email' | phase 2 | login/page.tsx |
| `view_category` | category_name, product_count | phase 2 | CategoryTracker.tsx (client wrapper in [category]/page.tsx) |
| `select_delivery_method` | method, fee | phase 3 | DeliveryCalculator.tsx |
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
| 1. Any landing | URL | `/` |
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

## Changelog

| Date | Change |
|---|---|
| 2026-04-18 | Initial setup — 15 events, 7 goals, 3 funnels across 3 phases |
