import { HAS_REMOTE_API } from '@/lib/cache-policy';

type NextInit = RequestInit & { next?: { revalidate?: number; tags?: string[] } };

/**
 * How many times a transient failure is retried before it is allowed through.
 *
 * A build renders the whole catalogue against the API in one burst, and the
 * small box behind it answers a stray request with a 503 under that load. One
 * such blip used to fail the entire build — or, under ISR, pin a 404 for the
 * whole TTL — over a hiccup that was gone a moment later. A couple of quick
 * retries absorb it. A real 404 is never retried (it is an answer, not a blip),
 * and a failure that outlives the retries still throws, so persistent damage
 * still stops the build rather than shipping an empty grid.
 */
const MAX_ATTEMPTS = 3;

/** 200ms, 400ms, … with jitter so parallel prerenders don't retry in lockstep. */
function backoffMs(attempt: number): number {
  return 200 * 2 ** (attempt - 1) + Math.floor(Math.random() * 150);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * `fetch`, retrying a transient server error (5xx) or a network-level failure.
 *
 * Returns the final `Response` — including a 4xx, or a 5xx from the last attempt
 * — for the caller to interpret; only a network error that outlived the retries
 * is rethrown. 4xx is deterministic (a 404 is a real answer) and never retried.
 */
async function fetchRetrying(url: string, init?: NextInit): Promise<Response> {
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, init);
      if (res.status >= 500 && attempt < MAX_ATTEMPTS) {
        await sleep(backoffMs(attempt));
        continue;
      }
      return res;
    } catch (err) {
      if (attempt >= MAX_ATTEMPTS) throw err;
      await sleep(backoffMs(attempt));
    }
  }
}

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
 * is retried a few times (see `fetchRetrying`) and, if it still will not
 * answer, throws. Next never writes a failed fetch to the data cache, a build
 * stops instead of shipping the damage, and an ISR revalidation that fails
 * keeps the last good page up.
 *
 * `HAS_REMOTE_API` is the escape hatch for CI, where there is no API on the
 * runner and a build rendered from nothing is a perfectly good compile check.
 */
export async function fetchJsonOrNull<T>(url: string, init?: NextInit): Promise<T | null> {
  try {
    const res = await fetchRetrying(url, init);
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
    const res = await fetchRetrying(url, init);
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
