/**
 * What the homepage's `Menu` schema tells a crawler each featured product
 * costs.
 *
 * This used to be `Number(p.base_price).toFixed(2)` — a column that is zero
 * for every product priced entirely through its modifiers, which is most of
 * the catalogue (every brownie, every cookie). Every one of those published
 * `price: "0.00"` to Google and to any answer engine reading the page.
 *
 * `buildJsonLd` must go through `offerPrice` (built on the single
 * `computeFromPrice`, shared with the product page and the card) instead, and
 * never publish a `0.00` offer.
 */

import { describe, expect, it } from 'vitest';

import { buildJsonLd } from './page';
import type { Category, Product } from '@/lib/types';

function option(id: string, price: number, is_active = true) {
  return { id, modifier_id: 'm1', name: id, translations: {}, sku: id, price, calories: null, is_active, display_order: 0 };
}

function category(id: string, slug: string): Category {
  return {
    id,
    name: slug,
    translations: {},
    slug,
    reference: null,
    description: null,
    image_url: null,
    display_order: 0,
    is_active: true,
    product_count: 1,
  };
}

function product(
  overrides: Partial<Product> & { category_id: string },
): Product {
  return {
    id: overrides.id ?? 'p1',
    category_id: overrides.category_id,
    name: overrides.name ?? 'Brown Butter Cookies',
    slug: overrides.slug ?? 'brown-butter-cookies',
    sku: null,
    description: null,
    translations: {},
    base_price: overrides.base_price ?? 0,
    calories: null,
    preparation_time: null,
    is_sold_by_weight: false,
    is_stock_product: false,
    stock_quantity: 0,
    image_urls: [],
    is_active: true,
    labels: ['bestseller'],
    display_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    product_modifiers: overrides.product_modifiers ?? [],
    category: null,
  };
}

/** Pulls the `Offer`s out of the Menu node's `MenuItem`s, in order. */
function menuOffers(jsonLd: ReturnType<typeof buildJsonLd>) {
  const menu = jsonLd['@graph'].find((n: { '@type': string }) => n['@type'] === 'Menu') as {
    hasMenuSection: { hasMenuItem: { offers?: { price: string } }[] }[];
  };
  return menu.hasMenuSection.flatMap((s) => s.hasMenuItem.map((i) => i.offers));
}

describe("the homepage Menu schema's item prices", () => {
  it('quotes the modifier floor, not the zero base price, for a modifier-priced product', () => {
    const cat = category('c1', 'cookies');
    const p = product({
      category_id: 'c1',
      base_price: 0,
      product_modifiers: [
        {
          id: 'pm1',
          modifier_id: 'm1',
          minimum_options: 1,
          maximum_options: 1,
          free_options: 0,
          unique_options: true,
          display_order: 0,
          modifier: { id: 'm1', reference: 'r', name: 'Size', translations: {}, options: [option('3pc', 40), option('6pc', 80)] },
        },
      ],
    });

    const jsonLd = buildJsonLd([cat], [p], null, 'en');
    const [offer] = menuOffers(jsonLd);

    expect(offer).toBeDefined();
    expect(offer!.price).toBe('40.00');
  });

  it('takes the global minimum across two groups, not the first', () => {
    const cat = category('c1', 'cookies');
    const p = product({
      category_id: 'c1',
      base_price: 0,
      product_modifiers: [
        {
          id: 'pm1',
          modifier_id: 'm1',
          minimum_options: 0,
          maximum_options: 1,
          free_options: 0,
          unique_options: true,
          display_order: 0,
          modifier: { id: 'm1', reference: 'r', name: 'A', translations: {}, options: [option('a', 50)] },
        },
        {
          id: 'pm2',
          modifier_id: 'm2',
          minimum_options: 0,
          maximum_options: 1,
          free_options: 0,
          unique_options: true,
          display_order: 1,
          modifier: { id: 'm2', reference: 'r', name: 'B', translations: {}, options: [option('b', 10)] },
        },
      ],
    });

    const jsonLd = buildJsonLd([cat], [p], null, 'en');
    const [offer] = menuOffers(jsonLd);

    expect(offer!.price).toBe('10.00');
  });

  it('omits the offer instead of publishing 0.00 for a product with an empty modifier group', () => {
    const cat = category('c1', 'cookies');
    const p = product({
      category_id: 'c1',
      base_price: 0,
      product_modifiers: [
        {
          id: 'pm1',
          modifier_id: 'm1',
          minimum_options: 1,
          maximum_options: 1,
          free_options: 0,
          unique_options: true,
          display_order: 0,
          modifier: { id: 'm1', reference: 'r', name: 'Empty', translations: {}, options: [] },
        },
      ],
    });

    const jsonLd = buildJsonLd([cat], [p], null, 'en');
    const [offer] = menuOffers(jsonLd);

    expect(offer).toBeUndefined();
  });

  it('states the plain base price for a product with no modifiers', () => {
    const cat = category('c1', 'cakes');
    const p = product({ category_id: 'c1', base_price: 120, product_modifiers: [] });

    const jsonLd = buildJsonLd([cat], [p], null, 'en');
    const [offer] = menuOffers(jsonLd);

    expect(offer!.price).toBe('120.00');
  });
});
