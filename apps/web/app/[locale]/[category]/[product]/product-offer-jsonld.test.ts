/**
 * What the product page's JSON-LD `Offer` states as the price.
 *
 * This used to compute its own floor: `hasModifierPrices` checked every
 * option for `price > 0` with no `is_active` filter, and `minExtra` took
 * `Math.min()` of a group's options with no guard against an empty array —
 * `Math.min()` of nothing is `Infinity`, and a modifier group left with no
 * active options published that straight into `Offer.price`.
 *
 * `buildProductOffer` must go through `offerPrice` (the single
 * `computeFromPrice`, shared with the card and the homepage's Menu schema)
 * instead, take the global minimum across every group, and never publish
 * `Infinity` or `0.00`.
 */

import { describe, expect, it } from 'vitest';

import { buildProductOffer } from './page';
import type { Product, ProductModifier } from '@/lib/types';

function option(id: string, price: number, is_active = true) {
  return { id, modifier_id: 'm1', name: id, translations: {}, sku: id, price, calories: null, is_active, display_order: 0 };
}

function modifierGroup(
  id: string,
  minimum_options: number,
  options: ReturnType<typeof option>[],
): ProductModifier {
  return {
    id,
    modifier_id: `mod-${id}`,
    modifier: { id: `mod-${id}`, reference: 'r', name: id, translations: {}, options },
    minimum_options,
    maximum_options: options.length,
    free_options: 0,
    unique_options: true,
    display_order: 0,
  };
}

function product(base_price: number, product_modifiers: ProductModifier[]): Product {
  return {
    id: 'p1',
    category_id: 'c1',
    name: 'Test Product',
    slug: 'test-product',
    sku: null,
    description: null,
    translations: {},
    base_price,
    calories: null,
    preparation_time: null,
    is_sold_by_weight: false,
    is_stock_product: false,
    stock_quantity: 0,
    image_urls: [],
    is_active: true,
    labels: [],
    display_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    product_modifiers,
    category: null,
  };
}

const OPTS = { offerUrl: 'https://meltingmomentscakes.com/en/cookies/test-product', defaultDeliveryFee: 20 };

describe('buildProductOffer', () => {
  it('quotes the modifier floor for a two-group product, taking the global minimum', () => {
    const p = product(0, [
      modifierGroup('pm1', 0, [option('a', 50)]),
      modifierGroup('pm2', 0, [option('b', 10)]),
    ]);

    const offer = buildProductOffer(p, OPTS);

    expect(offer).toBeDefined();
    expect(offer!.price).toBe('10.00');
    expect(offer!.priceCurrency).toBe('AED');
  });

  it('never emits Infinity for a modifier group with no options', () => {
    const p = product(0, [modifierGroup('pm1', 1, [])]);

    const offer = buildProductOffer(p, OPTS);

    // Nothing left to price once the only group is empty — the offer is
    // dropped rather than stating a price at all.
    expect(offer).toBeUndefined();
  });

  it('ignores inactive options when computing the floor', () => {
    const p = product(0, [modifierGroup('pm1', 1, [option('cheap-but-off', 5, false), option('real', 45)])]);

    const offer = buildProductOffer(p, OPTS);

    expect(offer!.price).toBe('45.00');
  });

  it('never publishes 0.00 — omits the offer for a genuinely unpriced product', () => {
    const p = product(0, []);

    expect(buildProductOffer(p, OPTS)).toBeUndefined();
  });

  it('states the plain base price for a product with no modifiers', () => {
    const p = product(65, []);

    const offer = buildProductOffer(p, OPTS);

    expect(offer!.price).toBe('65.00');
  });

  it('marks availability from is_active', () => {
    const active = buildProductOffer({ ...product(30, []), is_active: true }, OPTS);
    const inactive = buildProductOffer({ ...product(30, []), is_active: false }, OPTS);

    expect(active!.availability).toBe('https://schema.org/InStock');
    expect(inactive!.availability).toBe('https://schema.org/OutOfStock');
  });
});
