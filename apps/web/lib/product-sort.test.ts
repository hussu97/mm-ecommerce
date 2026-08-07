import { describe, it, expect } from 'vitest';
import {
  DEFAULT_PRODUCT_SORT,
  parseProductSort,
  productSortLabel,
  productSortOptions,
} from './product-sort';

describe('parseProductSort', () => {
  it('keeps the orders the listing offers', () => {
    expect(parseProductSort('price_asc')).toBe('price_asc');
    expect(parseProductSort('price_desc')).toBe('price_desc');
    expect(parseProductSort('newest')).toBe('newest');
  });

  it('falls back to the default when absent', () => {
    expect(parseProductSort(undefined)).toBe(DEFAULT_PRODUCT_SORT);
    expect(parseProductSort('')).toBe(DEFAULT_PRODUCT_SORT);
  });

  it('ignores orders the UI cannot show as selected', () => {
    // The API accepts these; the control has no row for them, so a hand-edited
    // URL must not leave the select disagreeing with the list beneath it.
    expect(parseProductSort('category')).toBe(DEFAULT_PRODUCT_SORT);
    expect(parseProductSort('name')).toBe(DEFAULT_PRODUCT_SORT);
    expect(parseProductSort('; drop table')).toBe(DEFAULT_PRODUCT_SORT);
  });
});

describe('productSortOptions', () => {
  it('uses the translation when the key is seeded', () => {
    const t = (key: string) => ({ 'plp.sort_price_asc': 'السعر: من الأقل للأعلى' })[key] ?? key;
    const labels = Object.fromEntries(productSortOptions(t).map((o) => [o.value, o.label]));
    expect(labels.price_asc).toBe('السعر: من الأقل للأعلى');
  });

  it('shows English rather than the raw key while a translation is missing', () => {
    const t = (key: string) => key;
    const options = productSortOptions(t);
    expect(options.map((o) => o.value)).toEqual(['newest', 'price_asc', 'price_desc']);
    expect(options.every((o) => !o.label.startsWith('plp.'))).toBe(true);
    expect(productSortLabel(t)).toBe('Sort');
  });
});
