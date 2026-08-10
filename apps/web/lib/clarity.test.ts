import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clarity, mirrorToClarity, valueBand } from './clarity';

type Call = unknown[];

/** Install a fake tag and return the calls it receives. */
function installClarity(): Call[] {
  const calls: Call[] = [];
  window.clarity = ((...args: unknown[]) => {
    calls.push(args);
  }) as typeof window.clarity;
  return calls;
}

describe('clarity client', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    delete window.clarity;
  });

  afterEach(() => {
    // The queue polls on an interval; leaving one running leaks into the next
    // test and fires its calls against the next test's fake.
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    delete window.clarity;
  });

  it('calls straight through once the tag is loaded', () => {
    const calls = installClarity();

    clarity.event('add_to_cart');

    expect(calls).toEqual([['event', 'add_to_cart']]);
  });

  it('queues calls made before the tag loads, in order', () => {
    clarity.event('view_product');
    clarity.set('surface', 'pdp');

    const calls = installClarity();
    vi.advanceTimersByTime(250);

    expect(calls).toEqual([
      ['event', 'view_product'],
      ['set', 'surface', 'pdp'],
    ]);
  });

  /**
   * The tag is `afterInteractive` and replaces `window.clarity` when it lands,
   * so the target has to be read fresh on every attempt rather than captured.
   */
  it('keeps waiting well past the first few seconds', () => {
    clarity.event('begin_checkout');
    vi.advanceTimersByTime(10_000);

    const calls = installClarity();
    vi.advanceTimersByTime(250);

    expect(calls).toEqual([['event', 'begin_checkout']]);
  });

  it('survives a tag that throws', () => {
    const calls: Call[] = [];
    window.clarity = ((...args: unknown[]) => {
      calls.push(args);
      throw new Error('clarity exploded');
    }) as typeof window.clarity;

    expect(() => clarity.event('a')).not.toThrow();
    expect(() => clarity.event('b')).not.toThrow();
    // The failure of one call must not abandon the queue for the next.
    expect(calls).toHaveLength(2);
  });

  it('converts tag values to the string or array Clarity accepts', () => {
    const calls = installClarity();

    clarity.set('serviceable', false);
    clarity.set('item_count', 3);
    clarity.set('surface', 'cart');

    expect(calls).toEqual([
      ['set', 'serviceable', 'false'],
      ['set', 'item_count', '3'],
      ['set', 'surface', 'cart'],
    ]);
  });

  it('drops tag values that say nothing', () => {
    const calls = installClarity();

    clarity.set('reason', undefined);
    clarity.set('reason', null);
    clarity.set('reason', '');
    clarity.set('subtotal', NaN);
    clarity.set('payload', { nested: true });

    expect(calls).toEqual([]);
  });

  it('truncates a long tag value rather than sending it whole', () => {
    const calls = installClarity();

    clarity.set('title', 'x'.repeat(400));

    expect((calls[0][2] as string)).toHaveLength(255);
  });

  it('identifies with an id and never with a friendly name', () => {
    const calls = installClarity();

    clarity.identify('8f14e45f-ceea-467a-9c1b-2b4a1e2f9c31');

    // Exactly two arguments. A third would be the `custom-session-id`, a fifth
    // the `friendly-name` — which is where an email address would end up.
    expect(calls).toEqual([['identify', '8f14e45f-ceea-467a-9c1b-2b4a1e2f9c31']]);
  });

  it('ignores an empty user id', () => {
    const calls = installClarity();

    clarity.identify('');

    expect(calls).toEqual([]);
  });

  it('passes consent as consentv2, both flags together', () => {
    const calls = installClarity();

    clarity.consent(true);
    clarity.consent(false);

    expect(calls).toEqual([
      ['consentv2', { ad_Storage: 'granted', analytics_Storage: 'granted' }],
      ['consentv2', { ad_Storage: 'denied', analytics_Storage: 'denied' }],
    ]);
  });
});

describe('valueBand', () => {
  it('bands a basket into something you can filter on', () => {
    expect(valueBand(0)).toBe('0');
    expect(valueBand(49.99)).toBe('0-50');
    expect(valueBand(50)).toBe('50-100');
    expect(valueBand(143.75)).toBe('100-200');
    expect(valueBand(200)).toBe('200-500');
    expect(valueBand(1200)).toBe('500+');
  });

  it('refuses to invent a band for a number that is not one', () => {
    expect(valueBand(NaN)).toBe('unknown');
    expect(valueBand(-5)).toBe('unknown');
  });
});

describe('mirrorToClarity', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    delete window.clarity;
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    delete window.clarity;
  });

  it('sends the event and its allow-listed dimensions', () => {
    const calls = installClarity();

    mirrorToClarity('add_to_cart', {
      product_name: 'Salted Caramel Brownie',
      surface: 'pdp',
      quantity: 2,
    });

    expect(calls).toContainEqual(['event', 'add_to_cart']);
    expect(calls).toContainEqual(['set', 'product_name', 'Salted Caramel Brownie']);
    expect(calls).toContainEqual(['set', 'surface', 'pdp']);
    // `quantity` is a measurement, not a filter — see TAG_KEYS.
    expect(calls).not.toContainEqual(['set', 'quantity', '2']);
  });

  /**
   * The whole reason the payload is allow-listed rather than forwarded. Clarity
   * stores what it is sent; a free-text field is a filter with one value per
   * visitor and, in the case of a search box, occasionally a personal one.
   */
  it('never turns free text or personal data into a tag', () => {
    const calls = installClarity();

    mirrorToClarity('search_no_results', { query: 'my address is 12 x street' });
    mirrorToClarity('payment_failed', {
      order_number: 'MM-1',
      error_message: 'Your card was declined by the issuing bank',
      email: 'someone@example.com',
      phone: '+971500000000',
    });

    const tagKeys = calls.filter((c) => c[0] === 'set').map((c) => c[1]);
    expect(tagKeys).not.toContain('query');
    expect(tagKeys).not.toContain('error_message');
    expect(tagKeys).not.toContain('email');
    expect(tagKeys).not.toContain('phone');
    // The order number is deliberately kept — it is the join back to the admin.
    expect(tagKeys).toContain('order_number');
  });

  it('bands money instead of tagging the amount', () => {
    const calls = installClarity();

    mirrorToClarity('order_completed', { order_number: 'MM-2', total: 143.75, subtotal: 120 });

    expect(calls).toContainEqual(['set', 'value_band', '100-200']);
    expect(calls.filter((c) => c[0] === 'set' && c[1] === 'value_band')).toHaveLength(1);
    expect(calls.map((c) => c[1])).not.toContain('total');
  });

  it('upgrades the session on anything that reads as a failure', () => {
    for (const event of [
      'payment_failed',
      'api_error',
      'app_error',
      'product_unavailable',
      'delivery_unserviceable',
      'page_not_found',
      'payment_cancelled',
    ]) {
      const calls = installClarity();
      mirrorToClarity(event, undefined);
      expect(calls).toContainEqual(['upgrade', event]);
    }
  });

  it('upgrades the session on the moments worth watching end to end', () => {
    for (const event of ['order_completed', 'begin_checkout', 'view_checkout']) {
      const calls = installClarity();
      mirrorToClarity(event, undefined);
      expect(calls).toContainEqual(['upgrade', event]);
    }
  });

  it('leaves ordinary events unprioritised', () => {
    const calls = installClarity();

    mirrorToClarity('view_product', { product_name: 'A' });
    mirrorToClarity('faq_opened', { question: 'Do you deliver to Ajman?' });

    expect(calls.some((c) => c[0] === 'upgrade')).toBe(false);
  });

  it('sends the event even when there is no payload', () => {
    const calls = installClarity();

    mirrorToClarity('logout', undefined);

    expect(calls).toEqual([['event', 'logout']]);
  });
});
