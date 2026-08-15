'use client';

import { useEffect, useState } from 'react';

import { getSessionId, ordersApi } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import type { OrderPreview } from '@/lib/types';

interface Options {
  /**
   * False while a returned unpaid order owns the totals — its figures are
   * already written and are what the customer owes — and before the basket has
   * loaded, when there is nothing to price.
   */
  enabled: boolean;
  deliveryMethod: 'delivery' | 'pickup';
  latitude: number | null;
  longitude: number | null;
  /** The pin's typed address. Travels so the courier costs the same trip. */
  address: string;
  promoCode: string;
  email: string | null;
  phone: string | null;
  /**
   * The basket, as something to watch. The server reads the cart itself — the
   * browser does not send it — so this exists only to say "it moved, ask
   * again". Subtotal and item count together catch every change that can move a
   * total; a swap of two equally-priced cakes moves neither, and moves no
   * figure on this screen either.
   */
  subtotal: number;
  itemCount: number;
}

/**
 * The checkout's only source of money.
 *
 * Replaces the `deliveryApi.quote` call this screen used to make, rather than
 * sitting alongside it: `POST /orders/preview` prices the delivery and the
 * order in one server-side pass and returns both, so the fee on the delivery
 * line and the fee inside the total are the same number by construction. Two
 * endpoints meant two calls to the courier's live pricing API for one basket,
 * and two chances to show a figure that was never charged.
 *
 * Debounced for the reason the quote was: `address` is in the dependency list
 * and a customer typing "Villa 12, Al Barsha" fired one request per keystroke,
 * each of which can reach that pricing API. The delay is short enough that the
 * total lands while they are still reading the line they just typed, and the
 * cleanup cancels the pending timer, so only the last edit is priced.
 *
 * A failed request deliberately changes nothing: the previous preview stays on
 * screen. Blanking the totals because one background call timed out is worse
 * than showing figures that are one edit stale, and the server prices the order
 * again — bindingly — when the button is pressed.
 */
export function useOrderPreview({
  enabled, deliveryMethod, latitude, longitude, address,
  promoCode, email, phone, subtotal, itemCount,
}: Options): OrderPreview | null {
  const [preview, setPreview] = useState<OrderPreview | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      ordersApi
        .preview({
          delivery_method: deliveryMethod,
          latitude,
          longitude,
          address: address || null,
          promo_code: promoCode || null,
          session_id: getSessionId(),
          email,
          phone,
        })
        .then((next) => {
          if (cancelled) return;
          setPreview(next);
          // What this pin was actually priced at. Nothing recorded so far said
          // what customers are being quoted, how often free delivery lands, or
          // how often the answer is "nowhere near us" — and delivery pricing is
          // the lever this shop pulls most often. Only quotes with a pin behind
          // them: a quote for a blank address is the same answer every time and
          // would drown the real ones.
          if (latitude !== null && longitude !== null) {
            analytics.deliveryQuote({
              serviceable: next.delivery.serviceable !== false,
              delivery_fee: next.delivery.delivery_fee ?? undefined,
              base_fee: next.delivery.base_fee ?? undefined,
              free_applied: next.delivery.free_delivery_applied,
              free_available: next.delivery.free_delivery_available,
              free_threshold: next.delivery.free_threshold ?? undefined,
              subtotal: next.subtotal - next.discount_amount,
            });
          }
        })
        .catch(() => { /* leave the previous figures in place */ });
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [
    enabled, deliveryMethod, latitude, longitude, address,
    promoCode, email, phone, subtotal, itemCount,
  ]);

  return preview;
}
