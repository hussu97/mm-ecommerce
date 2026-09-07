/**
 * F-WEB-4: after the pin moves or the delivery method flips, the pay button
 * must stop quoting the last priced total — the fee, and sometimes whether
 * there is one at all, changed underneath it.
 *
 * `pricing` alone does not gate the button (see `OrderPreviewState.pricing`
 * and `checkout/page.tsx`'s `resolveCheckoutGate` call): the page only shows
 * "Calculating total…" while `pricing && preview === null`. So a `preview`
 * left over from before the change is a stale total sitting behind a
 * pressable pay button for the whole 400ms debounce (and the request time on
 * top of it). These tests pin that `latitude`/`longitude`/`deliveryMethod`
 * clear `preview` the instant they change, while a typed field that does not
 * change what is being priced — `address` — leaves the old total in place
 * for the debounce to refresh normally.
 */

import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { OrderPreview } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  getSessionId: vi.fn(() => 'sess-1'),
  deliveryQuote: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  ordersApi: { preview: mocks.preview },
  getSessionId: mocks.getSessionId,
}));

vi.mock('@/lib/analytics', () => ({
  analytics: { deliveryQuote: mocks.deliveryQuote },
}));

import { useOrderPreview, type OrderPreviewState } from './useOrderPreview';

function makePreview(total: number): OrderPreview {
  return {
    subtotal: 100,
    discount_amount: 0,
    delivery_fee: 10,
    low_order_fee: 0,
    vat_rate: 0.05,
    vat_amount: 5,
    total_excl_vat: total - 5,
    total,
    promo: null,
    delivery: {
      delivery_fee: 10,
      base_fee: 10,
      free_delivery_applied: false,
      free_delivery_available: true,
      free_threshold: 200,
      remaining_for_free: 100,
      zone_name: 'Zone A',
      in_known_zone: true,
      delivery_estimate: null,
      serviceable: true,
    },
    unavailable_items: [],
  };
}

type Props = Parameters<typeof useOrderPreview>[0];

const baseProps: Props = {
  enabled: true,
  deliveryMethod: 'delivery',
  latitude: 25.2,
  longitude: 55.3,
  address: 'Villa 1, Al Barsha',
  promoCode: '',
  email: null,
  phone: null,
  subtotal: 100,
  itemCount: 2,
};

/** Renders, waits for the debounce to settle, and returns a landed preview. */
async function renderSettled(overrides: Partial<Props> = {}) {
  const view = renderHook((props: Props) => useOrderPreview(props), {
    initialProps: { ...baseProps, ...overrides },
  });
  await waitFor(() => expect(view.result.current.preview).not.toBeNull(), { timeout: 2000 });
  return view;
}

describe('useOrderPreview — invalidating a stale total', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.preview.mockResolvedValue(makePreview(115));
  });

  const expectCleared = (result: { current: OrderPreviewState }) => {
    // Cleared the instant the invalidating prop changes — no need to await
    // the debounce or the mocked request at all.
    expect(result.current.preview).toBeNull();
  };

  it('clears the preview the moment the pin moves', async () => {
    const { result, rerender } = await renderSettled();
    expect(result.current.preview?.total).toBe(115);

    rerender({ ...baseProps, latitude: 25.3 });
    expectCleared(result);
  });

  it('clears the preview the moment longitude moves', async () => {
    const { result, rerender } = await renderSettled();
    expect(result.current.preview?.total).toBe(115);

    rerender({ ...baseProps, longitude: 55.9 });
    expectCleared(result);
  });

  it('clears the preview the moment delivery method flips to pickup', async () => {
    const { result, rerender } = await renderSettled();
    expect(result.current.preview?.total).toBe(115);

    rerender({ ...baseProps, deliveryMethod: 'pickup' });
    expectCleared(result);
  });

  it('leaves the preview in place when a non-invalidating field changes', async () => {
    const { result, rerender } = await renderSettled();
    expect(result.current.preview?.total).toBe(115);

    // Typing in the address box re-prices (via the debounce) but does not,
    // on its own, make the total already on screen wrong to show.
    rerender({ ...baseProps, address: 'Villa 1, Al Barsha, near the mosque' });
    expect(result.current.preview?.total).toBe(115);

    rerender({ ...baseProps, promoCode: 'WELCOME15' });
    expect(result.current.preview?.total).toBe(115);
  });

  it('still refetches and lands a fresh total after an invalidating change', async () => {
    const { result, rerender } = await renderSettled();
    expect(result.current.preview?.total).toBe(115);

    mocks.preview.mockResolvedValue(makePreview(140));
    rerender({ ...baseProps, deliveryMethod: 'pickup' });
    expect(result.current.preview).toBeNull();

    await waitFor(() => expect(result.current.preview?.total).toBe(140), { timeout: 2000 });
  });
});
