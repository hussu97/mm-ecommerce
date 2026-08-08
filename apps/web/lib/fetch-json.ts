import { HAS_REMOTE_API } from '@/lib/cache-policy';

type NextInit = RequestInit & { next?: { revalidate?: number; tags?: string[] } };

/**
 * A server-side GET that tells "this does not exist" apart from "I could not
 * ask".
 *
 * Both used to come back as `null`, and every caller turned `null` into
 * `notFound()` or an empty grid. That was survivable while every page was
 * rendered per request — the next visitor got a fresh attempt. Under ISR it is
 * not: a page rendered during a blip is *kept*, so one refused connection
 * turned a live product into a 404 held for the whole TTL, and a rate-limited
 * build turned the catalogue into an empty grid.
 *
 * So: a real 404 returns `null`, because the product genuinely is not there.
 * Anything else — a 5xx, a 503 from the rate limiter, a refused connection —
 * throws. Next never writes a failed fetch to the data cache, a build stops
 * instead of shipping the damage, and an ISR revalidation that fails keeps the
 * last good page up.
 *
 * `HAS_REMOTE_API` is the escape hatch for CI, where there is no API on the
 * runner and a build rendered from nothing is a perfectly good compile check.
 */
export async function fetchJsonOrNull<T>(url: string, init?: NextInit): Promise<T | null> {
  try {
    const res = await fetch(url, init);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`GET ${stripHost(url)}: HTTP ${res.status}`);
    return (await res.json()) as T;
  } catch (err) {
    if (HAS_REMOTE_API) throw err;
    return null;
  }
}

/** As above, for endpoints where "not found" is not a meaningful answer. */
export async function fetchJson<T>(url: string, init?: NextInit): Promise<T | null> {
  try {
    const res = await fetch(url, init);
    if (!res.ok) throw new Error(`GET ${stripHost(url)}: HTTP ${res.status}`);
    return (await res.json()) as T;
  } catch (err) {
    if (HAS_REMOTE_API) throw err;
    return null;
  }
}

/** Keep the host out of build logs; the path is the part that identifies it. */
function stripHost(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url;
  }
}
