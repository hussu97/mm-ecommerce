import { withFallback } from '@/lib/i18n/fallback';

export interface SortOption {
  value: string;
  label: string;
}

/**
 * The orders the listing pages offer.
 *
 * The API accepts more (`oldest`, `name`, `category`), but those are catalogue
 * tools rather than shopping ones, and every extra row in a phone's sort sheet
 * is one more thing to read past. Anything outside this list is ignored rather
 * than forwarded, so a hand-edited URL cannot reach an order the UI cannot then
 * show as selected.
 */
export const PRODUCT_SORTS = ['newest', 'price_asc', 'price_desc'] as const;

export type ProductSort = (typeof PRODUCT_SORTS)[number];

export const DEFAULT_PRODUCT_SORT: ProductSort = 'newest';

/** The sort named in the URL, or the default when it is absent or unknown. */
export function parseProductSort(raw: string | undefined): ProductSort {
  return PRODUCT_SORTS.includes(raw as ProductSort) ? (raw as ProductSort) : DEFAULT_PRODUCT_SORT;
}

type TFunction = (key: string, params?: Record<string, string | number>) => string;

export function productSortOptions(t: TFunction): SortOption[] {
  return [
    { value: 'newest', label: withFallback(t, 'sort.newest', 'Newest') },
    { value: 'price_asc', label: withFallback(t, 'sort.price_asc', 'Price: low to high') },
    { value: 'price_desc', label: withFallback(t, 'sort.price_desc', 'Price: high to low') },
  ];
}

export function productSortLabel(t: TFunction): string {
  return withFallback(t, 'sort.label', 'Sort');
}
