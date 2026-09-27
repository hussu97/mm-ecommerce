import type { Product } from '@/lib/types';

/**
 * Whether a product can be bought right now.
 *
 * Two ways to be sold out, and the product page used to know only one. A
 * counted-stock product at zero was always shown as "Out of Stock". A product
 * that every kitchen had marked as out was a 404 instead: the API would not
 * serve the page at all. It is now served with `is_available: false`, so
 * both cases end up here. The product page, its JSON-LD `Offer` and its
 * add-to-cart all read this one answer, so none of them can disagree.
 */
export function isSoldOut(product: Pick<Product, 'is_available' | 'is_stock_product' | 'stock_quantity'>): boolean {
  return product.is_available === false || (product.is_stock_product && product.stock_quantity <= 0);
}
