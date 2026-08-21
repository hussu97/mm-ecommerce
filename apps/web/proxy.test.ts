import { describe, it, expect } from 'vitest';
import { NextRequest } from 'next/server';
import { config, proxy } from './proxy';

/**
 * A visitor who lands on `/checkout` — from a shared link, a QR code, a browser
 * autocomplete of a path they typed once — must not meet a 404. And a fresh
 * visitor should get the language their device asked for, falling back to
 * Arabic: the shop is in Sharjah and serves the UAE, so a browser asking for
 * something we do not speak is likelier to read Arabic than to have chosen
 * English on purpose.
 */

function request(path: string, headers: Record<string, string> = {}) {
  return new NextRequest(new URL(`https://meltingmomentscakes.com${path}`), { headers });
}

const location = (res: Response | undefined) => res?.headers.get('location');

describe('locale routing', () => {
  it('sends a locale-less page to a locale instead of a 404', () => {
    for (const path of ['/checkout', '/cart', '/about', '/cat-cookies', '/account/orders']) {
      const res = proxy(request(path, { 'accept-language': 'en-GB,en;q=0.9' }));
      expect(location(res), path).toBe(`https://meltingmomentscakes.com/en${path}`);
    }
  });

  it('honours the device language', () => {
    expect(location(proxy(request('/', { 'accept-language': 'ar-AE,ar;q=0.9' }))))
      .toBe('https://meltingmomentscakes.com/ar');
    expect(location(proxy(request('/', { 'accept-language': 'en-US,en;q=0.9' }))))
      .toBe('https://meltingmomentscakes.com/en');
  });

  it('falls back to Arabic when the device asks for something we do not speak', () => {
    expect(location(proxy(request('/', { 'accept-language': 'fr-FR,fr;q=0.9' }))))
      .toBe('https://meltingmomentscakes.com/ar');
  });

  it('falls back to Arabic when the device says nothing at all', () => {
    expect(location(proxy(request('/')))).toBe('https://meltingmomentscakes.com/ar');
  });

  it('takes the highest-ranked language it can actually serve', () => {
    // The device prefers French, but Arabic is the best of what we have.
    expect(location(proxy(request('/', { 'accept-language': 'fr;q=0.9,ar;q=0.8,en;q=0.7' }))))
      .toBe('https://meltingmomentscakes.com/ar');
  });

  it('lets a remembered choice beat the device', () => {
    const req = request('/checkout', { 'accept-language': 'en-US,en;q=0.9' });
    req.cookies.set('mm_locale', 'ar');
    expect(location(proxy(req))).toBe('https://meltingmomentscakes.com/ar/checkout');
  });

  it('leaves a path that already names a locale alone', () => {
    expect(location(proxy(request('/ar/checkout')))).toBeNull();
    expect(location(proxy(request('/en/checkout')))).toBeNull();
  });

  it('does not touch the files that are not pages', () => {
    for (const path of ['/robots.txt', '/sitemap.xml', '/llms.txt', '/api/health', '/_next/static/x.js']) {
      expect(location(proxy(request(path))), path).toBeNull();
    }
  });

  /**
   * The analytics endpoint is not a page. It has no dot in it, so it used to
   * fall through to the locale rule and every tracked event paid for a 307
   * before it reached Umami — including the ones fired as the page was being
   * replaced, which are the ones that cannot afford a second trip.
   */
  it('leaves the analytics proxy alone in every language', () => {
    const cases: Record<string, string>[] = [
      {},
      { 'accept-language': 'en-US,en;q=0.9' },
      { 'accept-language': 'fr-FR' },
    ];
    for (const headers of cases) {
      expect(location(proxy(request('/vague/api/send', headers)))).toBeNull();
      expect(location(proxy(request('/vague/v.js', headers)))).toBeNull();
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
  it('leaves the Sentry tunnel alone in every language', () => {
    const cases: Record<string, string>[] = [
      {},
      { 'accept-language': 'en-US,en;q=0.9' },
      { 'accept-language': 'ar-AE,ar;q=0.9' },
      { 'accept-language': 'fr-FR' },
    ];
    for (const headers of cases) {
      expect(location(proxy(request('/monitoring', headers)))).toBeNull();
    }
  });

  it('still sends a page whose slug merely starts with the tunnel path to a locale', () => {
    // Matched exactly rather than as a prefix, so "monitoring" stays available
    // as an ordinary word a slug may begin with.
    for (const path of ['/monitoring-cakes', '/monitoringly']) {
      expect(location(proxy(request(path, { 'accept-language': 'en' }))), path).toBe(
        `https://meltingmomentscakes.com/en${path}`,
      );
    }
  });

  /**
   * The analytics prefix is an ordinary word, not a reserved one, so a slug can
   * legitimately begin with it. Skipping on the prefix alone would strand
   * `/vaguely-chocolate` at a 404 instead of sending it to a language.
   */
  it('still sends a page whose slug merely starts with the analytics prefix to a locale', () => {
    for (const path of ['/vaguely-chocolate', '/vague-cookies', '/vagueness']) {
      expect(location(proxy(request(path, { 'accept-language': 'en' }))), path).toBe(
        `https://meltingmomentscakes.com/en${path}`,
      );
    }
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
