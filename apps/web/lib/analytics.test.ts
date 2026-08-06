import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { analytics } from './analytics';

describe('analytics', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    delete window.umami;
  });

  afterEach(() => {
    // The queue polls on an interval; leaving one running leaks into the next
    // test and fires its events against the next test's mock.
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
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
    vi.advanceTimersByTime(250);

    expect(track).toHaveBeenCalledWith('order_completed', {
      order_number: 'MM-1',
      total: 5,
      payment_provider: 'stripe',
      delivery_method: 'pickup',
      item_count: 1,
    });
  });

  /**
   * The old queue scheduled four timers up front and gave up after three
   * seconds. A third-party script that takes longer than that on a phone is
   * ordinary, and every event queued behind it was lost with no trace.
   */
  it('keeps waiting well past the first few seconds', () => {
    analytics.beginCheckout({ item_count: 1, subtotal: 10 });

    vi.advanceTimersByTime(10_000);
    const track = vi.fn();
    window.umami = { track };
    vi.advanceTimersByTime(250);

    expect(track).toHaveBeenCalledWith('begin_checkout', { item_count: 1, subtotal: 10 });
  });

  it('sends queued events in the order they were fired', () => {
    analytics.viewProduct({ product_name: 'A', category: 'c', price: 1, has_modifiers: false });
    analytics.addToCart({ product_name: 'A', variant_name: '', price: 1, quantity: 1 });

    const track = vi.fn();
    window.umami = { track };
    vi.advanceTimersByTime(250);

    // A later event must not overtake one still sitting in the queue.
    analytics.beginCheckout({ item_count: 1, subtotal: 1 });

    expect(track.mock.calls.map((c) => c[0])).toEqual([
      'view_product',
      'add_to_cart',
      'begin_checkout',
    ]);
  });

  it('stops polling once it has given up', () => {
    analytics.userLogin();
    vi.advanceTimersByTime(60_000);

    const track = vi.fn();
    window.umami = { track };
    vi.advanceTimersByTime(5_000);

    expect(track).not.toHaveBeenCalled();
  });
});
