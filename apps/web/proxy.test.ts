import { describe, it, expect } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from './proxy';

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
    for (const headers of [{}, { 'accept-language': 'en-US,en;q=0.9' }, { 'accept-language': 'fr-FR' }]) {
      expect(location(proxy(request('/umami/api/send', headers)))).toBeNull();
      expect(location(proxy(request('/umami/script.js', headers)))).toBeNull();
    }
  });
});
