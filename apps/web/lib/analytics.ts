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
};
