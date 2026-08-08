import { cache } from 'react';

import { RSC_API_BASE } from '@/lib/api';
import { CACHE_TAGS, CONTENT_TTL } from '@/lib/cache-policy';
import type { Category } from '@/lib/types';

/**
 * The category list.
 *
 * Two caches, doing two different jobs. `React.cache` collapses the several
 * callers inside one render — the locale layout wants it for the nav bar, the
 * homepage for its tiles and again for the Menu schema — into a single call.
 * The data cache underneath means that call usually is not made at all.
 *
 * See `CONTENT_TTL` for why the TTL is what it is.
 *
 * Callers that need only the live ones should use `getActiveCategories`.
 */
export const getCategories = cache(async (): Promise<Category[]> => {
  try {
    const res = await fetch(`${RSC_API_BASE}/categories`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return [];
    return (await res.json()) as Category[];
  } catch {
    return [];
  }
});

/** Live categories, in the order the admin arranged them. */
export const getActiveCategories = cache(async (): Promise<Category[]> => {
  const all = await getCategories();
  return all
    .filter((c) => c.is_active)
    .sort((a, b) => a.display_order - b.display_order);
});
