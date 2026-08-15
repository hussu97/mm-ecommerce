import { cache } from "react";

import type { Language } from "@/lib/types";
import { RSC_API_BASE } from "@/lib/api-server";
import { CACHE_TAGS, CONTENT_TTL, HAS_REMOTE_API, LANGUAGES_TTL } from "@/lib/cache-policy";
import { interpolate } from "./interpolate";

/**
 * Every UI string for one language.
 *
 * This was `cache: 'no-store'`, and the reason was a real incident: the API
 * deploy and the Vercel build run in parallel, so a build that snapshotted
 * translations before the startup seed had added a new key went on serving that
 * snapshot for five minutes — the checkout rendered
 * `checkout.estimated_delivery` at customers, verbatim, until it expired.
 *
 * `no-store` fixed that by never caching anything, at the price of making every
 * page on the site dynamic: a fetch that opts out of the data cache opts the
 * route out of static rendering with it, so nothing was ever served from the
 * CDN and every visit ran a render.
 *
 * `CONTENT_TTL` is the middle: the same failure is now bounded at a minute
 * rather than five, it expires on its own, and it costs one API call per minute
 * per locale instead of one per render. The `i18n` tag is here so that bound
 * can be taken to zero later without touching this file — a `revalidateTag`
 * from the admin on write is the real fix, and it slots in above this line.
 *
 * `React.cache` on top is a different thing again and does the other half: it
 * collapses the metadata pass, the layout and the page into one call within a
 * single render, which the data cache does not do for a cache miss.
 *
 * **It throws rather than falling back to `{}`, and that is the important
 * part.** An empty map is not a degraded page, it is a page of raw keys —
 * `nav.all` where the word "All" should be — and once caching exists, a
 * swallowed failure is a *cached* page of raw keys. This was not hypothetical:
 * a build run against the live API came back rate-limited, this returned `{}`,
 * and every prerendered route baked in the key names.
 *
 * Throwing is what makes every layer behave:
 *
 *   - during `next build`, the build fails instead of shipping the damage;
 *   - during an ISR revalidation, Next keeps serving the last good page;
 *   - a non-2xx is never written to the data cache in the first place.
 *
 * A 200 carrying `{}` is a different thing — that is the API's answer, and it
 * is honoured. So is a failure where `HAS_REMOTE_API` is false: CI builds with
 * no API on the runner, and a compile check has nothing to protect.
 */
export const getTranslations = cache(
  async (locale: string): Promise<Record<string, string>> => {
    try {
      const res = await fetch(`${RSC_API_BASE}/i18n/translations/${locale}`, {
        next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.i18n] },
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) {
        throw new Error(`translations ${locale}: HTTP ${res.status}`);
      }
      return await res.json();
    } catch (err) {
      if (HAS_REMOTE_API) throw err;
      return {};
    }
  },
);

/**
 * The language list. Falls back to an empty list on purpose, unlike the two
 * above: everything that reads it already copes — the layout derives direction
 * from the locale when the list is empty, and the switcher simply does not
 * render. A missing switcher is a smaller thing than a broken page.
 */
export const getLanguages = cache(async (): Promise<Language[]> => {
  try {
    const res = await fetch(`${RSC_API_BASE}/i18n/languages`, {
      next: { revalidate: LANGUAGES_TTL, tags: [CACHE_TAGS.i18n] },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
});

export function createT(translations: Record<string, string>) {
  return function t(key: string, params?: Record<string, string | number>): string {
    return interpolate(translations[key] ?? key, params);
  };
}
