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

function track(event: string, data?: Record<string, unknown>) {
  if (typeof window !== 'undefined' && window.umami) {
    window.umami.track(event, data);
  }
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

  checkoutStepComplete: (data: { step: 1 | 2 | 3; delivery_method?: string }) =>
    track('checkout_step_complete', data),

  paymentFailed: (data: { order_number: string; error_message: string }) =>
    track('payment_failed', data),

  checkoutError: (data: { step: 1 | 2 | 3; field: string }) =>
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
