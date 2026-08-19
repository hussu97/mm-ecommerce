/**
 * The server half of the storefront's API layer — Server Components, route
 * handlers, `sitemap.ts`, `generateMetadata`.
 *
 * Everything here fetches against `RSC_API_BASE`, never the public relative
 * base, for the reason documented on it. The `server-only` marker turns an
 * import from client code into a build error; browser code uses
 * `lib/api-client.ts`. The `fetchJson` helpers in `lib/fetch-json.ts` are this
 * side's error semantics and are re-exported here.
 */
import 'server-only';

import { API_BASE } from './api-base';
import { CACHE_TAGS, CONTENT_TTL } from './cache-policy';
import type { PickupBranch } from './types';

export { fetchJson, fetchJsonOrNull } from './fetch-json';

/**
 * Base URL for fetches that run on the server — Server Components, route
 * handlers, `sitemap.ts`, `generateMetadata`.
 *
 * `API_BASE` is a relative path in dev so the browser goes through the Next
 * rewrite and cookies stay same-origin, but Node's fetch cannot resolve a
 * relative URL with no request to resolve it against. Worse than throwing: in
 * a static prerender it never settles, so a `try`/`catch` fallback around it
 * never runs and the build worker is killed at its 60s timeout. Production
 * sets an absolute `NEXT_PUBLIC_API_URL`, which is why this only ever bites
 * locally. Every server-side fetch must use this, never `API_BASE`.
 */
export const RSC_API_BASE = API_BASE.startsWith('http')
  ? API_BASE
  : (process.env.NEXT_PRIVATE_API_HOST ?? 'http://localhost:8000') + '/api/v1';

export const cmsApi = {
  /**
   * Page content for one slug and locale.
   *
   * This was `cache: 'no-store'`, and it earned it: the 049 content migration
   * writes straight to Postgres, the Vercel build ran while the API was still
   * answering from its pre-migration Redis copy, and the stale answer stuck in
   * the data cache — one locale shipped the new home page and the other kept
   * serving the old one long after both the database and the API agreed on the
   * new content.
   *
   * Note what actually made that bad. Not that a cache existed, but that the
   * entry never expired on its own, so it outlived the disagreement that
   * created it and had to be found by a human. `CONTENT_TTL` is a minute; the
   * same mistake now corrects itself before anyone can report it, and the
   * `cms` tag is there for the on-demand purge that would make it instant.
   *
   * What the old comment got wrong is the cost side: "every page that reads the
   * CMS is already dynamic" was true, and was the problem rather than the
   * justification — `no-store` is *why* they were dynamic. The home page, the
   * about page, the FAQ and the privacy page have no per-visitor content in
   * them at all, and were being rendered from scratch for every visit to fetch
   * copy that changes a few times a year.
   *
   * Still throws rather than falling back: callers each have their own baked-in
   * copy to degrade to, and a thrown fetch is not written to the cache, so a
   * failed revalidation keeps serving the last good answer instead of caching
   * an empty one.
   */
  getPage: (slug: string, locale: string): Promise<{ slug: string; content: Record<string, unknown> }> => {
    return fetch(`${RSC_API_BASE}/cms/public/${slug}?locale=${locale}`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.cms] },
      signal: AbortSignal.timeout(8000),
    })
      .then(res => {
        if (!res.ok) throw new Error(`CMS fetch failed: ${res.status}`);
        return res.json();
      });
  },
};

export const branchesApi = {
  /**
   * The counters a customer may collect from. **Public**, and the same list the
   * checkout renders.
   *
   * Fetched rather than written into the copy. An address in a CMS answer is a
   * second copy of something the branch row already holds, and the day the shop
   * moves, one of the two is wrong with nothing to say so — the failure this
   * codebase has already been bitten by twice. The About page therefore asks
   * for the branch and prints what it gets.
   *
   * Resolves to `[]` rather than throwing: a missing "where to collect" block
   * is a worse page, but a page that 500s because a branch lookup timed out is
   * a broken one, and nothing else on About depends on this.
   */
  pickupPoints: (): Promise<PickupBranch[]> =>
    fetch(`${RSC_API_BASE}/branches/pickup-points`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.cms] },
      signal: AbortSignal.timeout(8000),
    })
      .then(res => (res.ok ? res.json() : []))
      .catch(() => []),
};
