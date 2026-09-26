'use client';

/**
 * The custom-order half of an order page: the `CustomOrderResponse` (with the
 * API's `actions` flags) and the one way to change it.
 *
 * Every mutation goes through `run`: the API's answer replaces the order, its
 * refusal is shown, and `onChanged` fires so the page can re-read the generic
 * order (status, timeline, courier, P&L) the same action moved.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, customOrdersApi, type CustomOrder } from '@/lib/api';
import { useToast } from '@/components/ui/feedback';

export type RunCustomAction = (
  key: string,
  fn: () => Promise<CustomOrder>,
  success?: string,
) => Promise<boolean>;

export function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function useCustomOrder(orderNumber: string | null, onChanged?: () => void) {
  const toast = useToast();
  const [order, setOrder] = useState<CustomOrder | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  // Held in a ref so a page that passes a fresh closure each render does not
  // re-create `run` (and re-render every card) on every render.
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  }, [onChanged]);

  const reload = useCallback(async () => {
    if (!orderNumber) return;
    setLoading(true);
    try {
      setOrder(await customOrdersApi.get(orderNumber));
      setLoadError('');
    } catch (err) {
      setLoadError(errorText(err, 'Could not load the custom order.'));
    } finally {
      setLoading(false);
    }
  }, [orderNumber]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const run: RunCustomAction = useCallback(
    async (key, fn, success) => {
      setBusy(key);
      setActionError('');
      try {
        setOrder(await fn());
        if (success) toast.success(success);
        changed.current?.();
        return true;
      } catch (err) {
        const msg = errorText(err, 'That did not go through.');
        setActionError(msg);
        toast.error(msg);
        return false;
      } finally {
        setBusy(null);
      }
    },
    [toast],
  );

  return { order, loading, loadError, busy, actionError, run, reload };
}
