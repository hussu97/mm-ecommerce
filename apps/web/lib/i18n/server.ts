import { cache } from "react";

import type { Language } from "@/lib/types";
import { RSC_API_BASE } from "@/lib/api-server";
import { CACHE_TAGS, CONTENT_TTL, HAS_REMOTE_API, LANGUAGES_TTL } from "@/lib/cache-policy";
import { interpolate } from "./interpolate";
import enSeed from "./seed/en.json";
import arSeed from "./seed/ar.json";

/**
 * True only for the one build step that has to fail rather than ship broken
 * data: `next build`'s prerender pass. Every other moment this process is
 * alive — `next dev`, ISR revalidation, an on-demand render — is runtime, and
 * a runtime fault must degrade instead of blanking the page. See
 * `getTranslations` and `getCategories` below.
 */
const IS_BUILD_PHASE = process.env.NEXT_PHASE === "phase-production-build";

/**
 * The repo-committed floor under `getTranslations`.
 *
 * Extracted from `apps/api/scripts/seed_i18n.py` (the actual source of truth
 * for every UI string) at the time this file was written, so it is real copy
 * rather than placeholder text — a customer who lands on it during an outage
 * reads the site, not raw translation keys. It only has to be *roughly*
 * current: `lastKnownGood` below overwrites it with the real thing the moment
 * one successful fetch lands in this isolate, so this file is what a cold
 * instance falls back to in the gap before that first fetch, and what every
 * instance falls back to if the API never answers at all.
 */
const SEED_TRANSLATIONS: Record<string, Record<string, string>> = {
  en: enSeed as Record<string, string>,
  ar: arSeed as Record<string, string>,
};

/**
 * Last-known-good, per locale, held across requests in this isolate.
 *
 * Seeded from the committed files above and overwritten on every successful
 * fetch, so a later blip serves the most recent real copy rather than falling
 * straight back to whatever shipped in the seed.
 */
const lastKnownGood: Record<string, Record<string, string>> = { ...SEED_TRANSLATIONS };

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
 * **It throws during a build, and only during a build.** An empty map is not
 * a degraded page, it is a page of raw keys — `nav.all` where the word "All"
 * should be — and a swallowed failure baked into a static page by `next
 * build` stays wrong until the next deploy. That was not hypothetical: a
 * build run against the live API once came back rate-limited, this returned
 * `{}`, and every prerendered route baked in the key names.
 *
 * A *runtime* fault is a different problem with a different fix. This same
 * throw, live during a 2026-09 outage, is why `app/[locale]/layout.tsx`
 * rejected on every request for three hours: `global-error.tsx` was supposed
 * to catch that and could not even render itself (see the note there), so
 * visitors got nothing branded at all. Throwing was the wrong move for a page
 * already being served to somebody — the right one is `lastKnownGood`, which
 * a request in this state serves without blocking on anything.
 *
 * So the rule is `IS_BUILD_PHASE`, not `HAS_REMOTE_API`:
 *
 *   - during `next build`'s prerender pass, this still throws — the build
 *     fails instead of shipping the damage, exactly as before;
 *   - at runtime (`next dev`, ISR revalidation, an on-demand render), a fault
 *     serves the last translations that actually loaded in this isolate, or
 *     the repo-committed seed if none ever did;
 *   - a non-2xx is still never written to the data cache;
 *   - `HAS_REMOTE_API` false (CI, no API on the runner) still returns `{}`,
 *     same as always — there is nothing to protect there and no seed to reach
 *     for.
 *
 * A 200 carrying `{}` is a different thing again — that is the API's answer,
 * and it is honoured (and also becomes the new `lastKnownGood`, empty as it
 * is; an admin genuinely clearing every string is not a fault this should
 * paper over).
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
      const data = (await res.json()) as Record<string, string>;
      lastKnownGood[locale] = data;
      return data;
    } catch (err) {
      if (!HAS_REMOTE_API) return {};
      if (IS_BUILD_PHASE) throw err;
      return lastKnownGood[locale] ?? SEED_TRANSLATIONS.en;
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
