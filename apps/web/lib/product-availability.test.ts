import { describe, expect, it } from 'vitest';

import { isSoldOut } from './product-availability';

const base = { is_stock_product: false, stock_quantity: 0 };

describe('isSoldOut', () => {
  it('is sold out when no kitchen can make it', () => {
    expect(isSoldOut({ ...base, is_available: false })).toBe(true);
  });

  it('is sold out when counted stock has run out', () => {
    expect(isSoldOut({ is_available: true, is_stock_product: true, stock_quantity: 0 })).toBe(true);
  });

  it('is on sale otherwise, including when the API does not say', () => {
    // Listings never carry `false`, and an older API omits the field entirely.
    expect(isSoldOut({ ...base, is_available: true })).toBe(false);
    expect(isSoldOut(base)).toBe(false);
    expect(isSoldOut({ is_stock_product: true, stock_quantity: 3 })).toBe(false);
  });
});
