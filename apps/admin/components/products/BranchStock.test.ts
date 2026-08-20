import { describe, expect, it } from 'vitest';

import { branchStockStatus } from './BranchStock';
import type { BranchProductAvailability } from '@/lib/types';

const PRODUCT = 'a05cb040-11b8-4a9c-99e0-1b38dc5fd61e';
const BRANCH = '2ecc7e57-e543-460a-bf2b-5ab5e4642f3b';
const NOON = new Date('2026-08-20T12:00:00Z').getTime();

function row(over: Partial<BranchProductAvailability> = {}): BranchProductAvailability {
  return {
    branch_id: BRANCH,
    product_id: PRODUCT,
    is_in_stock: false,
    is_active: true,
    price: null,
    out_of_stock_until: null,
    ...over,
  };
}

describe('what a branch says about a product', () => {
  it('reads no row as in stock', () => {
    // `branch_products` holds a row only where a branch differs from the
    // catalogue. Reading absence as unavailable would paint every branch red
    // for every product nobody has ever touched — which is most of them.
    expect(branchStockStatus([], PRODUCT, BRANCH, NOON)).toEqual({
      inStock: true,
      until: null,
    });
  });

  it('reads a row for another branch as saying nothing about this one', () => {
    const other = row({ branch_id: '747c717a-a8b6-48d3-ab34-a472f07585ae' });

    expect(branchStockStatus([other], PRODUCT, BRANCH, NOON).inStock).toBe(true);
  });

  it('reports an indefinite stockout with no end time', () => {
    const status = branchStockStatus([row()], PRODUCT, BRANCH, NOON);

    expect(status.inStock).toBe(false);
    expect(status.until).toBeNull();
  });

  it('reports when a timed stockout comes back', () => {
    const until = '2026-08-20T16:30:00Z';
    const status = branchStockStatus([row({ out_of_stock_until: until })], PRODUCT, BRANCH, NOON);

    expect(status.inStock).toBe(false);
    expect(status.until).toBe(until);
  });

  it('lets a stockout expire on read, as the API does', () => {
    // The API compares the column with now every time it is asked, so a branch
    // that marked something out until 11:00 is selling it again at noon whether
    // or not a sweep has run. A console left open on a desk has to agree, or it
    // shows an hour that ended before lunch.
    const status = branchStockStatus(
      [row({ out_of_stock_until: '2026-08-20T11:00:00Z' })],
      PRODUCT,
      BRANCH,
      NOON,
    );

    expect(status.inStock).toBe(true);
  });

  it('treats a branch-deactivated product as off sale, clock or no clock', () => {
    // `is_active = false` is the branch saying it does not carry this at all.
    // It has no expiry and must not borrow one.
    const status = branchStockStatus(
      [row({ is_active: false, is_in_stock: true })],
      PRODUCT,
      BRANCH,
      NOON,
    );

    expect(status.inStock).toBe(false);
  });
});
