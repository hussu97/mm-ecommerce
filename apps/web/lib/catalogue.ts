import { cache } from 'react';

import { RSC_API_BASE } from '@/lib/api-server';
import { CACHE_TAGS, CONTENT_TTL, HAS_REMOTE_API } from '@/lib/cache-policy';
import type { Category } from '@/lib/types';

/**
 * True only for `next build`'s prerender pass — see the matching constant and
 * comment in `lib/i18n/server.ts`, which this mirrors for the same reason.
 */
const IS_BUILD_PHASE = process.env.NEXT_PHASE === 'phase-production-build';

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
 * Throws during a build rather than returning `[]`, for the same reason
 * `getTranslations` does: an empty list here is a storefront with no
 * navigation, no category tiles and a hero whose every slide has been
 * filtered out as pointing at a dead category. Baked into a static page by
 * `next build`, that is a broken site served until the next deploy.
 *
 * At runtime the trade flips. A category fetch that 504s mid-outage should not
 * take the *whole page* down with it — `app/[locale]/layout.tsx` awaits this
 * alongside `getTranslations`, and this was one of the two throws that turned
 * a transient API blip into `global-error.tsx`, which used to fail to render
 * at all (see the note there). `[]` there is a storefront with no nav bar; a
 * customer looking at a real page beats one looking at the browser's bare
 * error screen.
 *
 * So: throw only during `next build`'s prerender pass (`IS_BUILD_PHASE`),
 * return `[]` for every other failure, including one where `HAS_REMOTE_API`
 * is false — that is CI, which never had a build to protect here either.
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
    if (HAS_REMOTE_API && IS_BUILD_PHASE) throw err;
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
