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
 * A `next build` prerenders every product and category page against a live API,
 * and that API may be mid-deploy: a blue/green cutover serves a few seconds of
 * 5xx while the new colour drains in. A single failed fetch used to abort the
 * whole build (`categories: HTTP 503`). So the build — and only the build, a
 * runtime request must not make a visitor wait out an outage — retries a
 * transient failure with backoff long enough to outlast a cutover before it
 * gives up. Mirrors the same guard `getTranslations` already carries; categories
 * was the one build fetch that never got it.
 */
const BUILD_RETRY_DELAYS_MS = [500, 1000, 2000, 4000, 8000];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

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
async function fetchCategories(): Promise<Category[]> {
  const res = await fetch(`${RSC_API_BASE}/categories`, {
    next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.catalogue] },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) {
    throw new Error(`categories: HTTP ${res.status}`);
  }
  return (await res.json()) as Category[];
}

export const getCategories = cache(async (): Promise<Category[]> => {
  // Only the build both retries and, in the end, throws. At runtime a single
  // attempt then `[]` keeps the page up during an outage (the trade explained
  // above); with no API it is CI, which has no build to protect either.
  const retries = IS_BUILD_PHASE && HAS_REMOTE_API ? BUILD_RETRY_DELAYS_MS : [];
  for (let attempt = 0; ; attempt++) {
    try {
      return await fetchCategories();
    } catch (err) {
      if (attempt < retries.length) {
        await sleep(retries[attempt]);
        continue;
      }
      if (IS_BUILD_PHASE && HAS_REMOTE_API) throw err;
      return [];
    }
  }
});

/** Live categories, in the order the admin arranged them. */
export const getActiveCategories = cache(async (): Promise<Category[]> => {
  const all = await getCategories();
  return all
    .filter((c) => c.is_active)
    .sort((a, b) => a.display_order - b.display_order);
});
