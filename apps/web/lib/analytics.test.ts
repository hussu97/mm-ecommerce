import { beforeEach, describe, expect, it, vi } from 'vitest';

import { analytics } from './analytics';

describe('analytics', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    delete window.umami;
  });

  it('tracks immediately when Umami is loaded', () => {
    const track = vi.fn();
    window.umami = { track };

    analytics.beginCheckout({ item_count: 2, subtotal: 50 });

    expect(track).toHaveBeenCalledWith('begin_checkout', {
      item_count: 2,
      subtotal: 50,
    });
  });

  it('queues events fired before Umami loads', () => {
    analytics.orderCompleted({
      order_number: 'MM-1',
      total: 5,
      payment_provider: 'stripe',
      delivery_method: 'pickup',
      item_count: 1,
    });

    const track = vi.fn();
    window.umami = { track };
    vi.advanceTimersByTime(100);

    expect(track).toHaveBeenCalledWith('order_completed', {
      order_number: 'MM-1',
      total: 5,
      payment_provider: 'stripe',
      delivery_method: 'pickup',
      item_count: 1,
    });
  });
});
