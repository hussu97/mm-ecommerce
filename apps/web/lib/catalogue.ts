import { cache } from 'react';

import { RSC_API_BASE } from '@/lib/api';
import type { Category } from '@/lib/types';

/**
 * The category list, fetched at most once per request.
 *
 * Three places want this on a single homepage render — the locale layout for
 * the nav bar, the page for its tiles, and `buildJsonLd` for the Menu schema —
 * and each of them used to call `/categories` itself. `cache: 'no-store'` opts
 * out of Next's request memoisation, so those were three real calls to the API
 * for one byte-identical answer.
 *
 * `React.cache` memoises for the life of one request only, so this keeps the
 * freshness `no-store` was chosen for: the API answers from Redis and drops the
 * key on any admin write, and this layer cannot hold anything past the response
 * it was built for.
 *
 * Callers that need only the live ones should use `getActiveCategories`.
 */
export const getCategories = cache(async (): Promise<Category[]> => {
  try {
    const res = await fetch(`${RSC_API_BASE}/categories`, {
      cache: 'no-store',
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
