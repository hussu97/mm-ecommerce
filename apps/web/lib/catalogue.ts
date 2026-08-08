import { cache } from 'react';

import { RSC_API_BASE } from '@/lib/api';
import { CACHE_TAGS, CONTENT_TTL, HAS_REMOTE_API } from '@/lib/cache-policy';
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
/**
 * Throws rather than returning `[]` on a failed fetch, for the same reason
 * `getTranslations` does: an empty list here is a storefront with no navigation,
 * no category tiles and a hero whose every slide has been filtered out as
 * pointing at a dead category. Cached, that is a broken site served for the
 * whole TTL.
 *
 * Failing loudly instead means a build that cannot reach the API stops, and an
 * ISR revalidation that cannot reach it keeps the last good page up. A 200
 * carrying `[]` is honoured — that is the API saying there are no categories —
 * and so is a failure where `HAS_REMOTE_API` is false, which is CI.
 */
export const getCategories = cache(async (): Promise<Category[]> => {
  try {
    const res = await fetch(`${RSC_API_BASE}/categories`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) {
      throw new Error(`categories: HTTP ${res.status}`);
    }
    return (await res.json()) as Category[];
  } catch (err) {
    if (HAS_REMOTE_API) throw err;
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
