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

function track(event: string, data?: Record<string, unknown>) {
  if (typeof window === 'undefined') return;

  // Queue first, then drain — so an event fired while something older is still
  // waiting cannot overtake it.
  queuedEvents.push({ event, data });
  if (!flushQueuedEvents()) scheduleFlush();
}

export const analytics = {
  // ─── Purchase funnel ──────────────────────────────────────────────────────
  addToCart: (data: {
    product_name: string;
    variant_name: string;
    price: number;
    quantity: number;
  }) => track('add_to_cart', data),

  removeFromCart: (data: { product_name: string }) =>
    track('remove_from_cart', data),

  beginCheckout: (data: { item_count: number; subtotal: number }) =>
    track('begin_checkout', data),

  promoApplied: (data: { code: string; discount: number }) =>
    track('promo_applied', data),

  orderCompleted: (data: {
    order_number: string;
    total: number;
    payment_provider: string;
    delivery_method: string;
    item_count: number;
  }) => track('order_completed', data),

  // ─── Phase 1: Conversion funnel gaps ─────────────────────────────────────
  viewProduct: (data: {
    product_name: string;
    category: string;
    price: number;
    has_modifiers: boolean;
  }) => track('view_product', data),

  checkoutStepComplete: (data: { step: 1 | 2; delivery_method?: string }) =>
    track('checkout_step_complete', data),

  paymentFailed: (data: { order_number: string; error_message: string }) =>
    track('payment_failed', data),

  checkoutError: (data: { step: 1 | 2; field: string }) =>
    track('checkout_error', data),

  // ─── Phase 2: Acquisition & auth ─────────────────────────────────────────
  search: (data: { query: string; result_count: number }) =>
    track('search', data),

  userSignup: () => track('user_signup', { method: 'email' }),

  userLogin: () => track('user_login', { method: 'email' }),

  viewCategory: (data: { category_name: string; product_count: number }) =>
    track('view_category', data),

  // ─── Phase 3: Behavioral & engagement ────────────────────────────────────
  selectDeliveryMethod: (data: { method: 'delivery' | 'pickup'; fee: number }) =>
    track('select_delivery_method', data),

  promoFailed: (data: { code: string; reason: string }) =>
    track('promo_failed', data),

  contactClick: (data: { channel: 'whatsapp' | 'email' | 'instagram' | 'map' }) =>
    track('contact_click', data),

  localeChanged: (data: { from: string; to: string }) =>
    track('locale_changed', data),

  orderTracked: (data: { order_number: string; status: string }) =>
    track('order_tracked', data),
};
