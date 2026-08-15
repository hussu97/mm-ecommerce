'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import { ordersApi } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import type { Order } from '@/lib/types';

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

    setRestoring(true);
    // An order exists and has not been paid for. This is the most expensive
    // thing that happens on the site and until now it was invisible: the
    // customer got a warning toast, the order sat unpaid, and nothing
    // anywhere counted it. Fired before the lookup, so a lookup that also
    // fails does not hide the cancellation underneath it.
    analytics.paymentCancelled({ order_number: returnOrder });
    ordersApi.get(returnOrder)
      .then((order) => {
        setRetryOrder(order);
        addToast(t('checkout.payment_cancelled'), 'warning');
      })
      .catch(() => addToast(t('checkout.payment_cancelled'), 'warning'))
      .finally(() => setRestoring(false));
  // Once, on arrival. The query string does not change under this page: the
  // gateway lands on it, and everything after that is a form being filled in.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { retryOrder, setRetryOrder, restoring };
}
