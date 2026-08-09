// ─── Umami custom event tracking ─────────────────────────────────────────────
// window.umami is injected by the Umami script loaded in layout.tsx.
// Falls back silently when the script isn't present (dev / ad-blocker).

declare global {
  interface Window {
    umami?: {
      track: (event: string, data?: Record<string, unknown>) => void;
    };
  }
}

type QueuedEvent = {
  event: string;
  data?: Record<string, unknown>;
};

const queuedEvents: QueuedEvent[] = [];

/**
 * How long to keep waiting for the Umami script, and how often to look.
 *
 * The script is `afterInteractive`, so anything a page tracks on mount races
 * it. This used to be four fixed timers — 100ms, 500ms, 1.5s, 3s, all scheduled
 * from the first queued event — and after the last of them nothing ever looked
 * again: an event queued on a connection where a third-party script takes more
 * than three seconds was dropped for good, silently, with no retry and no
 * error. That window is smallest on exactly the page it matters most on, the
 * order confirmation, which arrives as a fresh document from the payment
 * gateway and tracks `order_completed` as soon as its first API call returns.
 *
 * A plain poll to a generous ceiling costs a handful of property reads and
 * cannot fail that way. It stops the moment the script appears.
 */
const POLL_INTERVAL_MS = 250;
const POLL_CEILING_MS = 30_000;

let pollTimer: number | null = null;

function flushQueuedEvents(): boolean {
  if (typeof window === 'undefined' || !window.umami) return false;

  while (queuedEvents.length > 0) {
    const queued = queuedEvents.shift()!;
    window.umami.track(queued.event, queued.data);
  }
  return true;
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function scheduleFlush() {
  if (pollTimer !== null || typeof window === 'undefined') return;

  const deadline = Date.now() + POLL_CEILING_MS;
  pollTimer = window.setInterval(() => {
    if (flushQueuedEvents() || Date.now() > deadline) stopPolling();
  }, POLL_INTERVAL_MS);
}

/**
 * Drop the properties that carry no information.
 *
 * Umami stores every property it is handed and shows it as its own filterable
 * row, so a `delivery_method: undefined` becomes a permanent empty column and a
 * `reason: ''` becomes a value you can filter by that means nothing. Callers
 * below pass plenty of optional fields — a promo code that may not exist, a fee
 * that is not knowable until a pin resolves — and none of them should have to
 * assemble their payload conditionally to keep the dashboard clean.
 *
 * `null` is dropped along with them. It reads as "we know this is nothing", but
 * Umami cannot tell it apart from a missing property when filtering, so the
 * honest thing is to leave it out and let absence say it.
 */
function clean(data?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!data) return undefined;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (value === undefined || value === null || value === '') continue;
    out[key] = value;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function track(event: string, data?: Record<string, unknown>) {
  if (typeof window === 'undefined') return;

  // Queue first, then drain — so an event fired while something older is still
  // waiting cannot overtake it.
  queuedEvents.push({ event, data: clean(data) });
  if (!flushQueuedEvents()) scheduleFlush();
}

/**
 * A short, stable slug for a failure — never the message the customer saw.
 *
 * Error text is translated, is rewritten whenever the copy is, and arrives from
 * the API as a full sentence. Recorded verbatim it makes `reason` a field with
 * one distinct value per incident, which is a field you cannot group by. What we
 * want to answer is "how often does *this kind* of thing happen", so the message
 * is reduced to a handful of buckets and the status code is sent alongside it
 * when there is one.
 */
export function failureReason(err: unknown): string {
  if (err === null || err === undefined) return 'unknown';
  const status = (err as { status?: number }).status;
  if (typeof status === 'number') {
    if (status === 401) return 'unauthenticated';
    if (status === 403) return 'forbidden';
    if (status === 404) return 'not_found';
    if (status === 409) return 'conflict';
    if (status === 422) return 'validation';
    if (status === 429) return 'rate_limited';
    if (status >= 500) return 'server_error';
    if (status >= 400) return 'client_error';
  }
  const message = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|load failed/i.test(message)) return 'network_error';
  if (/timeout|timed out/i.test(message)) return 'timeout';
  return 'unknown';
}

/**
 * Collapse a request path to its route, so `endpoint` stays groupable.
 *
 * `/orders/MM-20260808-0042` and `/products/salted-caramel-brownie` are two
 * events about the same two endpoints, and left alone they would make
 * `api_error` a list of every order number and product slug that ever failed —
 * thousands of rows, each with a count of one, and no way to see that the orders
 * endpoint is the one that is broken.
 */
export function normalisePath(path: string): string {
  return path
    .split('?')[0]
    .replace(/\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '/:id')
    .replace(/\/MM-[A-Z0-9-]+/gi, '/:orderNumber')
    .replace(/\/\d+/g, '/:id')
    // What is left after the known collections is a slug: a product, a category,
    // a blog post. The collection is the useful half.
    //
    // `[^:/]` on the first character, not `[^/]`: this rule runs last and would
    // otherwise rewrite what the rules above already replaced, turning
    // `/orders/:orderNumber` back into `/orders/:slug` and undoing the more
    // specific match. A segment that already begins with a colon is done.
    .replace(
      /^\/(products|categories|blog|orders|addresses)\/[^:/][^/]*/i,
      (_, collection) => `/${collection}/:slug`,
    );
}

/** Everywhere an event can be raised from. Keeps `surface` a closed set. */
export type Surface =
  | 'pdp'
  | 'tile'
  | 'modal'
  | 'cart'
  | 'checkout'
  | 'confirmation'
  | 'account'
  | 'signup'
  | 'home'
  | 'footer'
  | 'faq'
  | 'contact'
  | 'search'
  | 'category'
  | 'track';

/** The lists a product tile can be clicked out of. */
export type ProductList =
  | 'category'
  | 'search'
  | 'all_products'
  | 'home_featured'
  | 'recently_viewed'
  | 'carousel'
  | 'cart_upsell';

const CURRENCY = 'AED';

export const analytics = {
  // ─── Purchase funnel ──────────────────────────────────────────────────────
  addToCart: (data: {
    product_name: string;
    variant_name: string;
    price: number;
    quantity: number;
    surface?: Surface;
  }) =>
    track('add_to_cart', {
      ...data,
      // The line's worth, not the unit price. `price` alone made every
      // add-to-cart look like a one-item add, so the basket value building up
      // in front of a customer was not recoverable from the events at all.
      value: Number((data.price * data.quantity).toFixed(2)),
      currency: CURRENCY,
    }),

  addToCartFailed: (data: {
    product_name: string;
    surface: Surface;
    reason: string;
  }) => track('add_to_cart_failed', data),

  removeFromCart: (data: { product_name: string; surface?: Surface }) =>
    track('remove_from_cart', data),

  beginCheckout: (data: {
    item_count: number;
    subtotal: number;
    has_promo?: boolean;
  }) => track('begin_checkout', { ...data, currency: CURRENCY }),

  promoApplied: (data: {
    code: string;
    discount: number;
    surface?: Surface;
    subtotal?: number;
    from_tray?: boolean;
  }) => track('promo_applied', { ...data, currency: CURRENCY }),

  orderCompleted: (data: {
    order_number: string;
    total: number;
    payment_provider: string;
    delivery_method: string;
    item_count: number;
    subtotal?: number;
    delivery_fee?: number;
    low_order_fee?: number;
    discount?: number;
    promo_code?: string;
    is_guest?: boolean;
  }) => track('order_completed', { ...data, currency: CURRENCY }),

  // ─── Phase 1: Conversion funnel gaps ─────────────────────────────────────
  viewProduct: (data: {
    product_name: string;
    category: string;
    price: number;
    has_modifiers: boolean;
    slug?: string;
    in_stock?: boolean;
  }) => track('view_product', { ...data, currency: CURRENCY }),

  checkoutStepComplete: (data: { step: 1 | 2; delivery_method?: string }) =>
    track('checkout_step_complete', data),

  paymentFailed: (data: {
    order_number: string;
    error_message: string;
    reason?: string;
    provider?: string;
    total?: number;
    /** Which half broke: writing the order, or opening the gateway session. */
    stage?: 'create_order' | 'create_session';
  }) => track('payment_failed', { ...data, currency: CURRENCY }),

  checkoutError: (data: {
    step: 1 | 2;
    field: string;
    fields?: string;
    error_count?: number;
    delivery_method?: string;
  }) => track('checkout_error', data),

  // ─── Phase 2: Acquisition & auth ─────────────────────────────────────────
  search: (data: { query: string; result_count: number }) =>
    track('search', { ...data, has_results: data.result_count > 0 }),

  /**
   * A search that found nothing.
   *
   * Deliberately its own event rather than a filter on `search`. Umami goals and
   * funnel steps match on the event name, so "how many searches came up empty"
   * is only a chart you can build, a goal you can watch and a funnel you can
   * break out if it has a name of its own. It is also the single most actionable
   * list the shop has: a query with no results is either a product worth adding
   * or a synonym worth indexing.
   */
  searchNoResults: (data: { query: string; category?: string }) =>
    track('search_no_results', data),

  userSignup: (data?: { surface?: Surface }) =>
    track('user_signup', { method: 'email', ...data }),

  signupFailed: (data: { reason: string; status?: number }) =>
    track('signup_failed', data),

  userLogin: () => track('user_login', { method: 'email' }),

  loginFailed: (data: { reason: string; status?: number }) =>
    track('login_failed', data),

  logout: () => track('logout'),

  passwordResetRequested: () => track('password_reset_requested'),

  passwordResetCompleted: () => track('password_reset_completed'),

  passwordResetFailed: (data: {
    stage: 'request' | 'reset';
    reason: string;
  }) => track('password_reset_failed', data),

  viewCategory: (data: { category_name: string; product_count: number }) =>
    track('view_category', data),

  // ─── Phase 3: Behavioral & engagement ────────────────────────────────────
  selectDeliveryMethod: (data: { method: 'delivery' | 'pickup'; fee: number }) =>
    track('select_delivery_method', { ...data, currency: CURRENCY }),

  promoFailed: (data: {
    code: string;
    reason: string;
    surface?: Surface;
    subtotal?: number;
    from_tray?: boolean;
  }) => track('promo_failed', data),

  contactClick: (data: {
    channel: 'whatsapp' | 'email' | 'instagram' | 'map' | 'phone';
    surface?: Surface;
  }) => track('contact_click', data),

  localeChanged: (data: { from: string; to: string }) =>
    track('locale_changed', data),

  orderTracked: (data: {
    order_number: string;
    status: string;
    delivery_method?: string;
  }) => track('order_tracked', data),

  orderTrackFailed: (data: { reason: string }) =>
    track('order_track_failed', data),

  // ─── Phase 4a: Discovery & merchandising ─────────────────────────────────
  //
  // The hero, the promo banners and the category tiles are the most expensive
  // space on the site and until now not one click on them was recorded — so
  // "which slide sells" was unanswerable, and the carousel was ordered on taste.

  selectPromotion: (data: {
    creative: 'hero' | 'promo_banner' | 'category_tile' | 'cater' | 'usp';
    slot: number;
    title: string;
    target?: string;
  }) => track('select_promotion', data),

  /** A product tile clicked, and the list it was clicked out of. */
  selectItem: (data: {
    product_name: string;
    list: ProductList;
    position: number;
    price?: number;
  }) => track('select_item', { ...data, currency: CURRENCY }),

  sortProducts: (data: {
    sort: string;
    surface: Surface;
    category?: string;
  }) => track('sort_products', data),

  // ─── Phase 4b: Product ───────────────────────────────────────────────────

  /** Something a customer wanted to buy and could not. */
  productUnavailable: (data: {
    product_name: string;
    surface: Surface;
    reason: 'out_of_stock';
  }) => track('product_unavailable', data),

  /**
   * Which option, in which group. For a bakery this is the shape of demand:
   * which box size, which flavour, which icing — none of which is recoverable
   * from `add_to_cart`, whose `variant_name` is a string of option ids.
   */
  modifierSelected: (data: {
    product_name: string;
    group_name: string;
    option_name: string;
    price_delta?: number;
  }) => track('modifier_selected', data),

  optionsModalOpened: (data: {
    product_name: string;
    entry: 'select_options' | 'add_more';
  }) => track('options_modal_opened', data),

  // ─── Phase 4c: Cart ──────────────────────────────────────────────────────

  viewCart: (data: {
    item_count: number;
    subtotal: number;
    has_promo: boolean;
  }) => track('view_cart', { ...data, currency: CURRENCY }),

  cartEmpty: () => track('cart_empty'),

  updateCartQuantity: (data: {
    product_name: string;
    from: number;
    to: number;
    surface: Surface;
  }) =>
    track('update_cart_quantity', {
      ...data,
      direction: data.to > data.from ? 'increase' : 'decrease',
    }),

  cartActionFailed: (data: {
    // 'add' joined when the cart grew an add-on tray — a failure there is a
    // refused sale on the page furthest down the funnel, so it is worth
    // telling apart from a failed quantity tap.
    action: 'add' | 'update' | 'remove';
    reason: string;
    surface: Surface;
  }) => track('cart_action_failed', data),

  /**
   * The new-customer tray rendered. Without it the tray's apply rate has no
   * denominator — `promo_applied` counts the taps and nothing counts the
   * baskets that saw the offer and walked past it.
   */
  couponTrayShown: (data: {
    code: string;
    percent: number;
    first_orders_limit: number;
  }) => track('coupon_tray_shown', data),

  /**
   * The homepage strip rendered, once per visit that was not dismissed.
   *
   * Separate from `coupon_tray_shown` rather than sharing it with a `surface`,
   * because the two answer different questions and only one of them is new. The
   * tray is seen by somebody who has already filled a basket; this is seen
   * before anyone has decided to buy, which is the entire reason it exists — so
   * "does announcing the offer earlier bring in customers the tray never
   * reached" has to be readable without unpicking one number into two.
   */
  couponBannerShown: (data: {
    code: string;
    percent: number;
  }) => track('coupon_banner_shown', data),

  /** A tap on that strip. The numerator to the impression above. */
  couponBannerClicked: (data: { code: string }) => track('coupon_banner_clicked', data),

  // ─── Phase 4d: Checkout ──────────────────────────────────────────────────

  viewCheckout: (data: {
    item_count: number;
    subtotal: number;
    is_guest: boolean;
    has_saved_address: boolean;
  }) => track('view_checkout', { ...data, currency: CURRENCY }),

  /**
   * What the server priced this pin at.
   *
   * The single most useful new event on the site. Delivery in the UAE is priced
   * per zone — a flat fee near the kitchen, a live courier quote beyond it — and
   * nothing recorded so far says what customers are actually being quoted, how
   * often free delivery lands, or how often the answer is "we cannot deliver
   * there at all". Sent once per settled quote, not per keystroke.
   */
  deliveryQuote: (data: {
    serviceable: boolean;
    delivery_fee?: number;
    base_fee?: number;
    free_applied: boolean;
    free_available: boolean;
    free_threshold?: number;
    subtotal: number;
  }) => track('delivery_quote', { ...data, currency: CURRENCY }),

  /** A basket that cannot be delivered. The costliest silent failure we had. */
  deliveryUnserviceable: (data: { subtotal: number; item_count: number }) =>
    track('delivery_unserviceable', { ...data, currency: CURRENCY }),

  /**
   * A basket crossed the free-delivery line.
   *
   * `surface` was added when the cart started saying this too. Without it the
   * two screens are indistinguishable in Umami, and the whole point of moving
   * the nudge earlier is being able to tell whether the cart is where baskets
   * grow. `zone` is optional because only the cart knows it — the checkout has
   * a confirmed address by then and reads its threshold from the quote.
   */
  freeDeliveryUnlocked: (data: {
    threshold: number;
    subtotal: number;
    surface?: 'cart' | 'checkout';
    zone?: string;
  }) => track('free_delivery_unlocked', { ...data, currency: CURRENCY }),

  /** An add-on taken from the basket's tray. */
  cartAddonAdded: (data: {
    product_name: string;
    price: number;
    personalised: boolean;
  }) => track('cart_addon_added', { ...data, currency: CURRENCY }),

  /**
   * Somebody wrote a message on a line. Fired once per line, on the first save
   * that carries text — not per keystroke, and not again when they edit it.
   */
  personalisationEntered: (data: {
    product_name: string;
    type: string;
    length: number;
  }) => track('personalisation_entered', data),

  lowOrderFeeApplied: (data: { fee: number; subtotal: number }) =>
    track('low_order_fee_applied', { ...data, currency: CURRENCY }),

  paymentMethodSelected: (data: {
    method: string;
    delivery_method: string;
    total: number;
  }) => track('payment_method_selected', { ...data, currency: CURRENCY }),

  /** Came back from the gateway without paying. An order exists and is unpaid. */
  paymentCancelled: (data: { order_number: string; provider?: string }) =>
    track('payment_cancelled', data),

  paymentRetry: (data: { order_number: string; provider?: string }) =>
    track('payment_retry', data),

  /** The order was refused before a gateway was ever reached. */
  orderCreateFailed: (data: {
    reason: string;
    status?: number;
    delivery_method: string;
    total: number;
    has_promo: boolean;
  }) => track('order_create_failed', { ...data, currency: CURRENCY }),

  checkoutCartEmpty: () => track('checkout_cart_empty'),

  checkoutLoadFailed: () => track('checkout_load_failed'),

  pickupBranchSelected: (data: { branch_name: string }) =>
    track('pickup_branch_selected', data),

  savedAddressSelected: (data: { surface: Surface }) =>
    track('saved_address_selected', data),

  addressSaved: (data: {
    surface: Surface;
    has_pin: boolean;
    is_new: boolean;
  }) => track('address_saved', data),

  addressSaveFailed: (data: { surface: Surface; reason: string }) =>
    track('address_save_failed', data),

  addressDeleted: (data: { surface: Surface }) => track('address_deleted', data),

  /** How the pin got where it is — the fastest route wins the redesign. */
  locationPinSet: (data: {
    method: 'autocomplete' | 'map_tap' | 'drag' | 'current_location';
    surface: Surface;
  }) => track('location_pin_set', data),

  geolocationDenied: (data: { surface: Surface }) =>
    track('geolocation_denied', data),

  // ─── Phase 4e: Phone verification ────────────────────────────────────────
  //
  // Six events for one short flow, because it is a flow that gates a discount
  // and every step of it can fail for a different reason: an SMS that never
  // arrives is not the same problem as a code typed wrong.

  phoneVerifyStarted: (data: { surface: Surface }) =>
    track('phone_verify_started', data),

  phoneVerifySent: (data: { surface: Surface }) =>
    track('phone_verify_sent', data),

  phoneVerifySendFailed: (data: {
    surface: Surface;
    reason: 'rate_limited' | 'unavailable';
  }) => track('phone_verify_send_failed', data),

  phoneVerifyResent: (data: { surface: Surface }) =>
    track('phone_verify_resent', data),

  phoneVerifySucceeded: (data: { surface: Surface }) =>
    track('phone_verify_succeeded', data),

  phoneVerifyFailed: (data: { surface: Surface }) =>
    track('phone_verify_failed', data),

  // ─── Phase 4f: Global failures ───────────────────────────────────────────

  /**
   * Any API call that came back not-ok.
   *
   * One hook in `lib/api.ts` covers every endpoint the storefront has, present
   * and future, which is why this is here rather than at thirty call sites. The
   * path is normalised — see `normalisePath` — so `endpoint` stays a column you
   * can group by instead of a list of order numbers.
   */
  apiError: (data: { status: number; endpoint: string; method: string }) =>
    track('api_error', data),

  /** An unhandled render error — the "Something Went Wrong" screen. */
  appError: (data: { digest?: string; path: string }) =>
    track('app_error', data),

  pageNotFound: (data: { path: string; referrer?: string }) =>
    track('page_not_found', data),

  // ─── Phase 4g: Engagement ────────────────────────────────────────────────

  faqOpened: (data: { question: string; position: number }) =>
    track('faq_opened', data),
};
