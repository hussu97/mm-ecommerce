import { cache } from "react";

import type { Language } from "@/lib/types";
import { RSC_API_BASE } from "@/lib/api";
import { CONTENT_TTL } from "@/lib/cache-policy";

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
 */
export const getTranslations = cache(
  async (locale: string): Promise<Record<string, string>> => {
    try {
      const res = await fetch(`${RSC_API_BASE}/i18n/translations/${locale}`, {
        next: { revalidate: CONTENT_TTL, tags: ['i18n'] },
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) return {};
      return await res.json();
    } catch {
      return {};
    }
  },
);

export const getLanguages = cache(async (): Promise<Language[]> => {
  try {
    const res = await fetch(`${RSC_API_BASE}/i18n/languages`, {
      next: { revalidate: 300, tags: ['i18n'] },
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
    let value = translations[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        value = value.replace(`{${k}}`, String(v));
      }
    }
    return value;
  };
}
