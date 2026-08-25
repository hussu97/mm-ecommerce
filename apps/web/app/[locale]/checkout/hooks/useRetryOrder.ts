'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import { ordersApi } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import type { Order } from '@/lib/types';

/**
 * The `PaymentFailureReason` buckets the API can send, each with its own
 * `checkout.payment_failure.*` string. Listed rather than interpolated blindly
 * so a code we do not recognise (a newer API against an older web build) falls
 * back to a real sentence instead of rendering the raw key in a toast.
 */
const KNOWN_FAILURE_REASONS = new Set([
  'insufficient_funds',
  'expired_card',
  'incorrect_cvc',
  'incorrect_number',
  'incorrect_details',
  'card_not_supported',
  'authentication_required',
  'processing_error',
  'duplicate',
  'card_declined',
]);

/**
 * The toast for a returning unpaid order: what to say, and how loud.
 *
 * Prefers the normalised reason (Stripe → one localised sentence), falls back
 * to a gateway's own human message (Ziina, shown verbatim), and finally to the
 * plain "cancelled" line for an order that was abandoned rather than declined —
 * or for one whose failure webhook simply has not landed yet, the common race
 * when a customer bounces straight back from the payment page.
 */
function retryToast(
  order: Order,
  t: (k: string, p?: Record<string, string | number>) => string,
): { message: string; kind: 'warning' | 'error' } {
  if (order.status === 'payment_failed') {
    const reason = order.payment_failure_reason;
    if (reason && KNOWN_FAILURE_REASONS.has(reason)) {
      return { message: t(`checkout.payment_failure.${reason}`), kind: 'error' };
    }
    if (order.payment_failure_message) {
      return { message: order.payment_failure_message, kind: 'error' };
    }
    // Declined, but we were told nothing usable — a specific-enough default.
    return { message: t('checkout.payment_failure.card_declined'), kind: 'error' };
  }
  return { message: t('checkout.payment_cancelled'), kind: 'warning' };
}

/**
 * How long to wait before looking again when the order comes back still
 * `created`.
 *
 * The redirect and the `payment_intent.payment_failed` webhook race, and the
 * redirect usually wins — the customer is back on this page before Stripe has
 * told us why the card failed, so the first lookup sees an order that is still
 * `created` and all we can honestly say is "cancelled". A single re-fetch after
 * this grace gives the webhook time to land and turn that into the real reason.
 * Three seconds is comfortably longer than the webhook round-trip and short
 * enough that the upgrade still feels like part of the same moment.
 */
const PAYMENT_WEBHOOK_GRACE_MS = 3000;

/**
 * The order the customer already has, and came back to pay for.
 *
 * The gateway returns them to `?step=payment&order_number=…` when they cancel.
 * That order exists, is unpaid, and owns its own totals — a customer coming
 * back owes what it was priced at, not what today's settings would charge for
 * the same basket — so its presence turns most of this screen read-only,
 * including the preview that would otherwise re-price a basket that is no
 * longer there.
 *
 * `restoring` is why the empty-cart redirect has to wait. The cart was emptied
 * when the order was created, so until this settles the page must not decide
 * the basket is empty and discard the order the customer came back for.
 */
export function useRetryOrder(
  addToast: (message: string, kind: 'warning' | 'error' | 'success') => void,
  t: (k: string, p?: Record<string, string | number>) => string,
) {
  const searchParams = useSearchParams();
  const [retryOrder, setRetryOrder] = useState<Order | null>(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    const returnOrder = searchParams.get('order_number');
    if (searchParams.get('step') !== 'payment' || !returnOrder) return;

    // Guards against setting state or firing a toast after the customer has
    // navigated away, and against the delayed re-fetch outliving this effect.
    let live = true;
    let graceTimer: ReturnType<typeof setTimeout> | undefined;

    setRestoring(true);
    // An order exists and has not been paid for. This is the most expensive
    // thing that happens on the site and until now it was invisible: the
    // customer got a warning toast, the order sat unpaid, and nothing
    // anywhere counted it. Fired before the lookup, so a lookup that also
    // fails does not hide the cancellation underneath it.
    analytics.paymentCancelled({ order_number: returnOrder });
    ordersApi.get(returnOrder)
      .then((order) => {
        if (!live) return;
        setRetryOrder(order);
        const { message, kind } = retryToast(order, t);
        addToast(message, kind);

        // The webhook may not have landed yet. If the order is still `created`
        // we cannot tell a decline whose reason is in flight from a genuine
        // walk-away, so we said the neutral "cancelled" above — then look once
        // more after a grace, and *upgrade* to the specific reason only if the
        // order has since turned `payment_failed`. A still-`created` order (a
        // real cancellation) says nothing further, so there is no second toast
        // to cry wolf with.
        if (order.status !== 'created') return;
        graceTimer = setTimeout(() => {
          ordersApi.get(returnOrder)
            .then((fresh) => {
              if (!live || fresh.status !== 'payment_failed') return;
              setRetryOrder(fresh);
              const upgraded = retryToast(fresh, t);
              addToast(upgraded.message, upgraded.kind);
            })
            // A failed re-fetch changes nothing — the customer already has the
            // neutral toast and the order in hand.
            .catch(() => {});
        }, PAYMENT_WEBHOOK_GRACE_MS);
      })
      // The lookup itself failed, so there is no order to read a reason off —
      // the plain cancelled line is all that can be said.
      .catch(() => { if (live) addToast(t('checkout.payment_cancelled'), 'warning'); })
      .finally(() => { if (live) setRestoring(false); });

    return () => {
      live = false;
      if (graceTimer) clearTimeout(graceTimer);
    };
  // Once, on arrival. The query string does not change under this page: the
  // gateway lands on it, and everything after that is a form being filled in.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { retryOrder, setRetryOrder, restoring };
}
