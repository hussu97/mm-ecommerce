import { describe, it, expect, vi, afterEach } from 'vitest';
import { NextRequest } from 'next/server';
import { config } from './proxy';

/**
 * A visitor who lands on `/checkout` — from a shared link, a QR code, a browser
 * autocomplete of a path they typed once — must not meet a 404. A fresh visitor
 * gets the language their device asked for. A device that asks for something we
 * do not speak gets Arabic: the shop is in Sharjah and serves the UAE. A client
 * that asks for *nothing* gets English, because that is only ever a crawler and
 * English is what every page declares as its `hreflang="x-default"`.
 *
 * And a URL that has moved is answered here, before anything renders — see the
 * note on `rulesCache` in `proxy.ts` for why it cannot be done in a page.
 */

type Rule = {
  from_path: string;
  to_path: string;
  is_prefix: boolean;
  status_code: number;
};

/** What migration 120 seeds, plus one English-only rule to prove exact wins. */
const RULES: Rule[] = [
  { from_path: '/cat-brownies', to_path: '/brownies', is_prefix: true, status_code: 308 },
  { from_path: '/cat-mixboxes', to_path: '/mix-boxes', is_prefix: true, status_code: 308 },
  { from_path: '/about-me', to_path: '/about', is_prefix: false, status_code: 308 },
  { from_path: '/en/press-kit', to_path: '/en/about', is_prefix: false, status_code: 301 },
];

/**
 * A fresh copy of the module with a known rule table.
 *
 * `resetModules` matters: the table is cached in module scope for a minute, so
 * without this the first suite to run would decide what every later one sees.
 */
async function loadProxy(rules: Rule[] | 'api-down' = []) {
  vi.resetModules();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (rules === 'api-down') throw new Error('connection refused');
      if (String(url).includes('/redirects/map')) {
        // Longest first, which is the order the API promises and the order
        // `matchRule` relies on to let a specific rule beat a general one.
        const sorted = [...rules].sort((a, b) => b.from_path.length - a.from_path.length);
        return new Response(JSON.stringify({ rules: sorted }), { status: 200 });
      }
      return new Response('', { status: 204 });
    }),
  );
  return (await import('./proxy')).proxy;
}

function request(path: string, headers: Record<string, string> = {}) {
  return new NextRequest(new URL(`https://meltingmomentscakes.com${path}`), { headers });
}

const location = (res: Response | undefined) => res?.headers.get('location');

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('locale routing', () => {
  it('sends a locale-less page to a locale instead of a 404', async () => {
    const proxy = await loadProxy();
    for (const path of ['/checkout', '/cart', '/about', '/cookies', '/account/orders']) {
      const res = await proxy(request(path, { 'accept-language': 'en-GB,en;q=0.9' }));
      expect(location(res), path).toBe(`https://meltingmomentscakes.com/en${path}`);
    }
  });

  it('honours the device language', async () => {
    const proxy = await loadProxy();
    expect(location(await proxy(request('/', { 'accept-language': 'ar-AE,ar;q=0.9' }))))
      .toBe('https://meltingmomentscakes.com/ar');
    expect(location(await proxy(request('/', { 'accept-language': 'en-US,en;q=0.9' }))))
      .toBe('https://meltingmomentscakes.com/en');
  });

  it('falls back to Arabic when the device asks for something we do not speak', async () => {
    const proxy = await loadProxy();
    expect(location(await proxy(request('/', { 'accept-language': 'fr-FR,fr;q=0.9' }))))
      .toBe('https://meltingmomentscakes.com/ar');
  });

  /**
   * The one that was wrong. Googlebot and Bingbot send no `accept-language`, so
   * the bare domain 307'd them to `/ar` while every page's markup declared
   * `x-default` as the English URL — which is why the English brand result on
   * Google carried an Arabic description of the shop. "You did not ask" and "we
   * cannot serve what you asked for" are different questions and no longer
   * share an answer.
   */
  it('sends a client that states no language to the x-default, English', async () => {
    const proxy = await loadProxy();
    expect(location(await proxy(request('/')))).toBe('https://meltingmomentscakes.com/en');
  });

  it('takes the highest-ranked language it can actually serve', async () => {
    const proxy = await loadProxy();
    // The device prefers French, but Arabic is the best of what we have.
    expect(location(await proxy(request('/', { 'accept-language': 'fr;q=0.9,ar;q=0.8,en;q=0.7' }))))
      .toBe('https://meltingmomentscakes.com/ar');
  });

  it('lets a remembered choice beat the device', async () => {
    const proxy = await loadProxy();
    const req = request('/checkout', { 'accept-language': 'en-US,en;q=0.9' });
    req.cookies.set('mm_locale', 'ar');
    expect(location(await proxy(req))).toBe('https://meltingmomentscakes.com/ar/checkout');
  });

  it('leaves a path that already names a locale alone', async () => {
    const proxy = await loadProxy();
    expect(location(await proxy(request('/ar/checkout')))).toBeNull();
    expect(location(await proxy(request('/en/checkout')))).toBeNull();
  });

  it('does not touch the files that are not pages', async () => {
    const proxy = await loadProxy();
    for (const path of ['/robots.txt', '/sitemap.xml', '/llms.txt', '/api/health', '/_next/static/x.js']) {
      expect(location(await proxy(request(path))), path).toBeNull();
    }
  });

  /**
   * The analytics endpoint is not a page. It has no dot in it, so it used to
   * fall through to the locale rule and every tracked event paid for a 307
   * before it reached Umami — including the ones fired as the page was being
   * replaced, which are the ones that cannot afford a second trip.
   */
  it('leaves the analytics proxy alone in every language', async () => {
    const proxy = await loadProxy();
    const cases: Record<string, string>[] = [
      {},
      { 'accept-language': 'en-US,en;q=0.9' },
      { 'accept-language': 'fr-FR' },
    ];
    for (const headers of cases) {
      expect(location(await proxy(request('/vague/api/send', headers)))).toBeNull();
      expect(location(await proxy(request('/vague/v.js', headers)))).toBeNull();
    }
  });

  /**
   * The Sentry tunnel is not a page either. It used to be answered with a 307
   * to `/ar/monitoring`, which is `[locale]/[category]` rendering a category
   * called "monitoring" — so every crash report the storefront filed was
   * redirected into a product listing and thrown away, and the listing then
   * asked the API for a category that does not exist. Thirty
   * `GET /api/v1/categories/monitoring 404`s in twelve hours, one per report
   * that never arrived.
   */
  it('leaves the Sentry tunnel alone in every language', async () => {
    const proxy = await loadProxy();
    const cases: Record<string, string>[] = [
      {},
      { 'accept-language': 'en-US,en;q=0.9' },
      { 'accept-language': 'ar-AE,ar;q=0.9' },
      { 'accept-language': 'fr-FR' },
    ];
    for (const headers of cases) {
      expect(location(await proxy(request('/monitoring', headers)))).toBeNull();
    }
  });

  it('still sends a page whose slug merely starts with the tunnel path to a locale', async () => {
    const proxy = await loadProxy();
    // Matched exactly rather than as a prefix, so "monitoring" stays available
    // as an ordinary word a slug may begin with.
    for (const path of ['/monitoring-cakes', '/monitoringly']) {
      expect(location(await proxy(request(path, { 'accept-language': 'en' }))), path).toBe(
        `https://meltingmomentscakes.com/en${path}`,
      );
    }
  });

  /**
   * The analytics prefix is an ordinary word, not a reserved one, so a slug can
   * legitimately begin with it. Skipping on the prefix alone would strand
   * `/vaguely-chocolate` at a 404 instead of sending it to a language.
   */
  it('still sends a page whose slug merely starts with the analytics prefix to a locale', async () => {
    const proxy = await loadProxy();
    for (const path of ['/vaguely-chocolate', '/vague-cookies', '/vagueness']) {
      expect(location(await proxy(request(path, { 'accept-language': 'en' }))), path).toBe(
        `https://meltingmomentscakes.com/en${path}`,
      );
    }
  });
});

describe('redirects', () => {
  const en = { 'accept-language': 'en-GB,en;q=0.9' };

  it('answers a moved category in the language the visitor was reading', async () => {
    const proxy = await loadProxy(RULES);
    const res = await proxy(request('/en/cat-brownies', en));
    expect(location(res)).toBe('https://meltingmomentscakes.com/en/brownies');
    expect(res?.status).toBe(308);

    const ar = await proxy(request('/ar/cat-brownies', en));
    expect(location(ar)).toBe('https://meltingmomentscakes.com/ar/brownies');
  });

  /**
   * One hop, not two. Answering the redirect before the locale rule is what
   * makes this a single 308 rather than a 307 to `/en/cat-brownies` followed by
   * a 308 to `/en/brownies` — two round trips for the visitor, and a chain that
   * search engines pass less through than a single jump.
   */
  it('sends a locale-less moved URL straight to the new page in one hop', async () => {
    const proxy = await loadProxy(RULES);
    expect(location(await proxy(request('/cat-brownies', en))))
      .toBe('https://meltingmomentscakes.com/en/brownies');
  });

  /**
   * The reason `is_prefix` exists. There are 36 product URLs nested under the
   * eight category slugs, all of them indexed, and an exact-match rule would
   * leave every one of them behind.
   */
  it('carries everything below a prefix rule across', async () => {
    const proxy = await loadProxy(RULES);
    expect(location(await proxy(request('/en/cat-mixboxes/mix-cookies-box-of-9', en))))
      .toBe('https://meltingmomentscakes.com/en/mix-boxes/mix-cookies-box-of-9');
  });

  it('does not carry a non-prefix rule past its own path', async () => {
    const proxy = await loadProxy(RULES);
    // `/about-me` moved; `/about-me-and-you` is simply not a page, and turning
    // it into one silently would be worse than a 404.
    expect(location(await proxy(request('/en/about-me-and-you', en)))).toBeNull();
  });

  it('keeps the query string the visitor arrived with', async () => {
    const proxy = await loadProxy(RULES);
    expect(location(await proxy(request('/en/cat-brownies?sort=price_asc', en))))
      .toBe('https://meltingmomentscakes.com/en/brownies?sort=price_asc');
  });

  it('normalises case and a trailing slash before matching', async () => {
    const proxy = await loadProxy(RULES);
    for (const path of ['/en/Cat-Brownies', '/en/cat-brownies/']) {
      expect(location(await proxy(request(path, en))), path)
        .toBe('https://meltingmomentscakes.com/en/brownies');
    }
  });

  it('lets a rule that names a locale keep its own locale and status', async () => {
    const proxy = await loadProxy(RULES);
    const res = await proxy(request('/en/press-kit', en));
    expect(location(res)).toBe('https://meltingmomentscakes.com/en/about');
    expect(res?.status).toBe(301);
    // Arabic has no such rule, so it falls through rather than being dragged
    // into an English page.
    expect(location(await proxy(request('/ar/press-kit', en)))).toBeNull();
  });

  it('leaves a path no rule covers alone', async () => {
    const proxy = await loadProxy(RULES);
    expect(location(await proxy(request('/en/brownies', en)))).toBeNull();
    expect(location(await proxy(request('/en/faq', en)))).toBeNull();
  });

  /**
   * A storefront that serves every page and forgets some redirects is a bad
   * morning. A storefront that 500s because a redirect table could not be read
   * is an outage — caused by the feature for handling URLs that no longer
   * exist.
   */
  it('fails open when the API cannot be reached', async () => {
    const proxy = await loadProxy('api-down');
    expect(location(await proxy(request('/en/cat-brownies', en)))).toBeNull();
    expect(location(await proxy(request('/cat-brownies', en))))
      .toBe('https://meltingmomentscakes.com/en/cat-brownies');
  });
});

/**
 * The matcher, not the function.
 *
 * These two have to agree: the body returning `NextResponse.next()` only
 * matters for a path the matcher let through in the first place, and a matcher
 * that skips too much strands a real page on a 404 without the body ever
 * getting a say. The anchor on `monitoring$` is the whole difference between
 * those two failures, so it is asserted rather than read.
 */
describe('proxy matcher', () => {
  const matches = (path: string) =>
    config.matcher.some((m) => new RegExp(`^${m}$`).test(path));

  it('skips the paths that are not pages', () => {
    for (const path of ['/monitoring', '/vague/api/send', '/api/health', '/_next/static/x.js', '/logo.png']) {
      expect(matches(path), path).toBe(false);
    }
  });

  it('still runs for real pages, including ones that start with a reserved word', () => {
    for (const path of ['/', '/checkout', '/cat-brownies', '/en/cat-brownies', '/monitoring-cakes', '/vaguely-chocolate']) {
      expect(matches(path), path).toBe(true);
    }
  });
});
