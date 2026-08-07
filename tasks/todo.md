# Umami event expansion — phase 4

## Why

Today's 19 events answer "how many people bought". They cannot answer **why the
other 97% did not**. Every failure the storefront can produce — an address we do
not deliver to, a payment that came back cancelled, a coupon the server refused,
an API that 500'd — is currently invisible: the customer sees a red toast and we
see nothing at all. The same is true of the merchandising: the hero carousel, the
promo banners and the category tiles are the most expensive real estate on the
site and not one click on them is recorded.

So: **every unhappy path gets an event, and every happy path gets attributes rich
enough to segment on.**

## Design rules for this phase

- Every failure carries a `reason` (a short stable slug, never a raw message) and,
  where it exists, a `status`.
- Every money event carries `currency: 'AED'` so the values are never ambiguous.
- Every event that can happen in more than one place carries `surface`.
- `api_error` normalises its path (`/orders/MM-1234` → `/orders/:id`) so the
  property does not explode into thousands of distinct values.
- No PII. No email, no phone, no address line, no raw coordinates.

---

## Plan

### 1. `lib/analytics.ts` — new API surface
- [x] Add `clean()` so `null`/`undefined`/`''` never reach Umami
- [x] Add the 40 new event helpers below
- [x] Enrich the 9 existing helpers with new attributes

### 2. Discovery & merchandising
- [x] `select_promotion` — HeroCarousel, PromoBanners, CategoryTiles, CaterSection
- [x] `select_item` — ProductCard, everywhere a tile is clicked
- [x] `sort_products` — SortSelect
- [x] `search_no_results` — SearchTracker

### 3. Product
- [x] `product_unavailable` — out-of-stock seen on PDP / tile
- [x] `modifier_selected` — which box size, which flavour
- [x] `options_modal_opened`
- [x] `add_to_cart_failed`
- [x] enrich `view_product`, `add_to_cart`, `remove_from_cart`

### 4. Cart
- [x] `view_cart`, `cart_empty`, `update_cart_quantity`, `cart_action_failed`
- [x] `coupon_tray_shown`
- [x] enrich `promo_applied`, `promo_failed` with `surface` + `subtotal`

### 5. Checkout — the richest seam
- [x] `view_checkout`
- [x] `delivery_quote` — fee, base fee, free-delivery state, serviceability
- [x] `delivery_unserviceable`
- [x] `free_delivery_unlocked`
- [x] `low_order_fee_applied`
- [x] `payment_method_selected`
- [x] `payment_cancelled` — returned from the gateway without paying
- [x] `payment_retry`
- [x] `order_create_failed`
- [x] `checkout_cart_empty`, `checkout_load_failed`
- [x] `pickup_branch_selected`
- [x] `address_saved`, `address_save_failed`, `address_deleted`, `saved_address_selected`
- [x] `location_pin_set`, `geolocation_denied`
- [x] enrich `checkout_error`, `payment_failed`, `order_completed`

### 6. Auth & account
- [x] `login_failed`, `signup_failed`, `logout`
- [x] `password_reset_requested`, `password_reset_completed`, `password_reset_failed`
- [x] `phone_verify_started|sent|send_failed|succeeded|failed|resent`

### 7. Global errors
- [x] `api_error` — one hook in `lib/api.ts` covers every endpoint
- [x] `app_error` — `app/error.tsx`
- [x] `page_not_found` — 404

### 8. Engagement
- [x] `faq_opened`
- [x] `order_track_failed`
- [x] enrich `contact_click` with `surface`, `order_tracked` with `delivery_method`

### 9. Docs & dashboard
- [x] Update `docs/umami-analytics-setup.md` — events table, goals, funnels, changelog
- [x] Rebuild goals in Umami
- [x] Rebuild funnels in Umami
- [x] Verify events arrive on the live site

---

## Review

_Filled in at the end._
