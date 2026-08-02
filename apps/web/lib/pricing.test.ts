import { describe, it, expect } from 'vitest';
import { computeFromPrice, isModifierPriced } from './pricing';
import type { Product } from './types';

function option(id: string, price: number, is_active = true, display_order = 0) {
  return { id, modifier_id: 'm1', name: id, translations: {}, sku: id, price, calories: null, is_active, display_order };
}

function product(base_price: number, groups: Array<{ min: number; max: number; options: ReturnType<typeof option>[] }>): Product {
  return {
    base_price,
    product_modifiers: groups.map((g, i) => ({
      id: `pm${i}`,
      modifier_id: 'm1',
      modifier: { id: 'm1', reference: 'r', name: 'g', translations: {}, is_active: true, options: g.options },
      minimum_options: g.min,
      maximum_options: g.max,
      free_options: 0,
      unique_options: true,
      display_order: i,
    })),
  } as unknown as Product;
}

describe('computeFromPrice', () => {
  it('returns base price when the product has no modifiers', () => {
    expect(computeFromPrice(product(70, []))).toBe(70);
  });

  it('adds the cheapest option of a required group', () => {
    expect(computeFromPrice(product(20, [{ min: 1, max: 1, options: [option('a', 15), option('b', 5)] }]))).toBe(25);
  });

  it('never reports 0 for a product priced entirely through its modifiers', () => {
    // Brown Butter Cookies: base 0, options 40/80/120. The homepage carousel
    // used raw base_price here and advertised "From 0.00 AED".
    const p = product(0, [{ min: 1, max: 1, options: [option('3pc', 40), option('6pc', 80), option('9pc', 120)] }]);
    expect(computeFromPrice(p)).toBe(40);
  });

  it('falls back to the cheapest option when every group is optional', () => {
    const p = product(0, [{ min: 0, max: 2, options: [option('a', 12), option('b', 30)] }]);
    expect(computeFromPrice(p)).toBe(12);
  });

  it('ignores inactive options', () => {
    const p = product(0, [{ min: 1, max: 1, options: [option('cheap', 5, false), option('real', 45)] }]);
    expect(computeFromPrice(p)).toBe(45);
  });

  it('stays at 0 when there is genuinely nothing priced', () => {
    expect(computeFromPrice(product(0, []))).toBe(0);
  });
});

describe('isModifierPriced', () => {
  it('is true when base price is 0 and modifiers exist', () => {
    expect(isModifierPriced(product(0, [{ min: 1, max: 1, options: [option('a', 40)] }]))).toBe(true);
  });

  it('is false when the product carries its own base price', () => {
    expect(isModifierPriced(product(30, [{ min: 1, max: 1, options: [option('a', 10)] }]))).toBe(false);
  });

  it('is false for a plain product', () => {
    expect(isModifierPriced(product(0, []))).toBe(false);
  });
});
