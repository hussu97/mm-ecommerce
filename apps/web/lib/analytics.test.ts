import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { analytics, failureReason, normalisePath } from './analytics';

describe('analytics', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    delete window.umami;
    delete window.clarity;
  });

  afterEach(() => {
    // The queue polls on an interval; leaving one running leaks into the next
    // test and fires its events against the next test's mock.
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    delete window.clarity;
  });

  it('tracks immediately when Umami is loaded', () => {
    const track = vi.fn();
    window.umami = { track };

    analytics.beginCheckout({ item_count: 2, subtotal: 50 });

    expect(track).toHaveBeenCalledWith('begin_checkout', {
      item_count: 2,
      subtotal: 50,
      currency: 'AED',
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
      currency: 'AED',
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

    expect(track).toHaveBeenCalledWith('begin_checkout', {
      item_count: 1,
      subtotal: 10,
      currency: 'AED',
    });
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

  /**
   * Empty properties make permanent empty columns in the dashboard, and every
   * helper below passes optional fields it may not have.
   */
  it('drops properties that carry no information', () => {
    const track = vi.fn();
    window.umami = { track };

    analytics.deliveryQuote({
      serviceable: true,
      delivery_fee: undefined,
      base_fee: 15,
      free_applied: false,
      free_available: true,
      free_threshold: undefined,
      subtotal: 80,
    });

    expect(track).toHaveBeenCalledWith('delivery_quote', {
      serviceable: true,
      base_fee: 15,
      // `false` survives — it is an answer, unlike the two undefined fees.
      free_applied: false,
      free_available: true,
      subtotal: 80,
      currency: 'AED',
    });
  });

  it('keeps a false and a zero, which are answers', () => {
    const track = vi.fn();
    window.umami = { track };

    analytics.deliveryQuote({
      serviceable: false,
      delivery_fee: 0,
      free_applied: false,
      free_available: false,
      subtotal: 0,
    });

    expect(track.mock.calls[0][1]).toMatchObject({
      serviceable: false,
      delivery_fee: 0,
      free_applied: false,
      free_available: false,
      subtotal: 0,
    });
  });

  it('sends the line value, not just the unit price', () => {
    const track = vi.fn();
    window.umami = { track };

    analytics.addToCart({ product_name: 'A', variant_name: '', price: 12.5, quantity: 3 });

    expect(track.mock.calls[0][1]).toMatchObject({ price: 12.5, quantity: 3, value: 37.5 });
  });
});

/**
 * Clarity is fed from inside `track()` rather than from the call sites, which is
 * what makes "every event" true of both tools at once. These tests are about
 * that wiring; what Clarity does with an event once it has it lives in
 * `clarity.test.ts`.
 */
describe('analytics → Clarity mirror', () => {
  let clarityCalls: unknown[][];

  beforeEach(() => {
    vi.useFakeTimers();
    delete window.umami;
    clarityCalls = [];
    window.clarity = ((...args: unknown[]) => {
      clarityCalls.push(args);
    }) as typeof window.clarity;
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    delete window.clarity;
  });

  it('mirrors an event to Clarity', () => {
    analytics.viewProduct({
      product_name: 'Salted Caramel Brownie',
      category: 'Brownies',
      price: 28,
      has_modifiers: true,
    });

    expect(clarityCalls).toContainEqual(['event', 'view_product']);
    expect(clarityCalls).toContainEqual(['set', 'category', 'Brownies']);
  });

  /**
   * Umami being blocked, absent or slow must not cost Clarity the event, and the
   * reverse. The two are fed from one call but they do not share a queue.
   */
  it('reaches Clarity even when Umami has not loaded', () => {
    expect(window.umami).toBeUndefined();

    analytics.beginCheckout({ item_count: 2, subtotal: 50 });

    expect(clarityCalls).toContainEqual(['event', 'begin_checkout']);
  });

  /**
   * `clean()` runs before either tool is called, so a field dropped for Umami
   * cannot reappear as a Clarity tag. Without this the two dashboards could
   * disagree about whether a property existed at all.
   */
  it('mirrors the cleaned payload, not the raw one', () => {
    analytics.promoFailed({ code: 'WELCOME15', reason: 'expired', surface: undefined });

    const tagKeys = clarityCalls.filter((c) => c[0] === 'set').map((c) => c[1]);
    expect(tagKeys).toContain('code');
    expect(tagKeys).toContain('reason');
    expect(tagKeys).not.toContain('surface');
  });

  it('prioritises the session for a completed order', () => {
    analytics.orderCompleted({
      order_number: 'MM-20260810-0042',
      total: 143.75,
      payment_provider: 'stripe',
      delivery_method: 'delivery',
      item_count: 3,
    });

    expect(clarityCalls).toContainEqual(['upgrade', 'order_completed']);
    expect(clarityCalls).toContainEqual(['set', 'order_number', 'MM-20260810-0042']);
    expect(clarityCalls).toContainEqual(['set', 'value_band', '100-200']);
  });
});

describe('failureReason', () => {
  it('buckets by status where there is one', () => {
    expect(failureReason({ status: 422 })).toBe('validation');
    expect(failureReason({ status: 429 })).toBe('rate_limited');
    expect(failureReason({ status: 503 })).toBe('server_error');
    expect(failureReason({ status: 418 })).toBe('client_error');
  });

  it('recognises a request that never arrived', () => {
    expect(failureReason(new TypeError('Failed to fetch'))).toBe('network_error');
  });

  it('never returns the message itself', () => {
    // The whole point: an error text is translated, rewritten with the copy and
    // arrives as a full sentence, so recorded verbatim it is a field with one
    // distinct value per incident.
    expect(failureReason(new Error('Coupon WELCOME15 has already been used'))).toBe('unknown');
  });
});

describe('normalisePath', () => {
  it('collapses the identifiers that would explode the property', () => {
    expect(normalisePath('/orders/MM-20260808-0042')).toBe('/orders/:orderNumber');
    expect(normalisePath('/products/salted-caramel-brownie')).toBe('/products/:slug');
    expect(normalisePath('/addresses/8f14e45f-ceea-467a-9c1b-2b4a1e2f9c31')).toBe('/addresses/:id');
    expect(normalisePath('/cart/items/12')).toBe('/cart/items/:id');
  });

  it('drops the query string and leaves plain routes alone', () => {
    expect(normalisePath('/products?search=brownie&page=2')).toBe('/products');
    expect(normalisePath('/delivery/quote')).toBe('/delivery/quote');
  });
});
