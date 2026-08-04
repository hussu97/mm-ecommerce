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
 * The cost is one intra-request call to an endpoint that answers from memory.
 */
export async function getTranslations(locale: string): Promise<Record<string, string>> {
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
}

export async function getLanguages(): Promise<Language[]> {
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
}

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
