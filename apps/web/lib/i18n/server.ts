import { cache } from "react";

import type { Language } from "@/lib/types";
import { RSC_API_BASE } from "@/lib/api";

/**
 * Every UI string for one language, read fresh on each render.
 *
 * Deliberately opts out of the Next data cache, for the same reason the CMS
 * fetch does. The API already holds these in Redis and drops the key the moment
 * the seed or the admin editor writes one, so a second cache out here buys
 * nothing and adds a layer nobody can see into or clear.
 *
 * It has already cost us once: the API deploy and the Vercel build run in
 * parallel, so a build that snapshotted translations before the startup seed
 * had added a new key went on serving that snapshot for five minutes — and the
 * checkout rendered `checkout.estimated_delivery` at customers, verbatim, until
 * it expired.
 *
 * `React.cache` is a different thing from that cache and does not reintroduce
 * it: the memo lives and dies with a single request, so a render still reads
 * whatever the API says *now*, it just stops asking three times. It was asking
 * three times — the metadata pass, the layout and the page each called this,
 * and `no-store` opts out of Next's request memoisation along with everything
 * else. Measured on one category render: 3 identical calls, and on a page
 * rendered a continent away from the API that was most of its TTFB.
 */
export const getTranslations = cache(
  async (locale: string): Promise<Record<string, string>> => {
    try {
      const res = await fetch(`${RSC_API_BASE}/i18n/translations/${locale}`, {
        cache: 'no-store',
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
      next: { revalidate: 300 },
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
