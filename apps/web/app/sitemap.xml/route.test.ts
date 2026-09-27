/**
 * The sitemap is served from the CDN, is never more than an hour old, and
 * lists every product page, sold out or not.
 *
 * On 2026-09-27 it still listed three sold-out cakes five hours after they sold
 * out (those pages were 404s then). Two stale-while-revalidate layers, each
 * moved only by sparse crawler requests, meant a rebuild read stale data. The
 * fix is a live build under a hard CDN expiry. These tests check each piece:
 * no data cache, an `s-maxage` with no `stale-while-revalidate`, and sold-out
 * pages included.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { GET, dynamic } from './route';

const json = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

function mockApi({ productPages = 1 } = {}) {
  const calls: { url: string; init?: RequestInit & { next?: unknown } }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes('/categories')) {
        return json([
          { slug: 'desserts', is_active: true, updated_at: '2026-09-27T00:00:00Z' },
          { slug: 'retired', is_active: false, updated_at: '2026-09-27T00:00:00Z' },
        ]);
      }
      if (url.includes('/products')) {
        const page = Number(new URL(url).searchParams.get('page'));
        return json({
          items: [
            {
              slug: page === 1 ? 'tiramisu' : `cake-${page}`,
              updated_at: '2026-09-27T00:00:00Z',
              category: { slug: 'desserts' },
              is_available: page !== 1,
            },
          ],
          pages: productPages,
        });
      }
      return json({ items: [{ slug: 'the-art-of-the-perfect-brownie', updated_at: '2026-09-01T00:00:00Z' }], pages: 1 });
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('sitemap.xml', () => {
  it('is built per request, never prerendered into ISR', () => {
    expect(dynamic).toBe('force-dynamic');
  });

  it('is cached by the CDN with a hard expiry', async () => {
    mockApi();
    const res = await GET();
    const cc = res.headers.get('Cache-Control') ?? '';

    expect(cc).toContain('s-maxage=3600');
    // The whole bug: serving the old copy while a rebuild runs in the background.
    expect(cc).not.toContain('stale-while-revalidate');
    expect(res.headers.get('Content-Type')).toContain('application/xml');
  });

  it('reads live data, never the fetch data cache', async () => {
    const calls = mockApi();
    await GET();

    expect(calls.length).toBeGreaterThan(0);
    for (const { url, init } of calls) {
      expect(init?.cache, url).toBe('no-store');
      expect(init?.next, url).toBeUndefined();
    }
  });

  it('lists a sold-out product, whose page is live', async () => {
    const calls = mockApi();
    const xml = await (await GET()).text();

    expect(calls.find((c) => c.url.includes('/products'))?.url).toContain('include_unavailable=true');
    expect(xml).toContain('<loc>https://meltingmomentscakes.com/en/desserts/tiramisu</loc>');
    expect(xml).toContain('<loc>https://meltingmomentscakes.com/ar/desserts/tiramisu</loc>');
  });

  it('keeps the shape crawlers already read', async () => {
    mockApi();
    const xml = await (await GET()).text();

    expect(xml).toContain('xmlns:xhtml="http://www.w3.org/1999/xhtml"');
    expect(xml).toContain(
      '<xhtml:link rel="alternate" hreflang="ar" href="https://meltingmomentscakes.com/ar/desserts" />',
    );
    expect(xml).toContain('<loc>https://meltingmomentscakes.com/en/blog/the-art-of-the-perfect-brownie</loc>');
    expect(xml).toContain('<priority>1</priority>');
    expect(xml).not.toContain('/retired');
    // 7 static + 1 category + 1 product + 1 post, in both locales.
    expect(xml.match(/<loc>/g)).toHaveLength(20);
  });

  it('follows every page of products', async () => {
    mockApi({ productPages: 3 });
    const xml = await (await GET()).text();

    expect(xml).toContain('/en/desserts/cake-2</loc>');
    expect(xml).toContain('/en/desserts/cake-3</loc>');
  });

  it('fails loudly rather than serving a partial sitemap', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 503 }))));
    await expect(GET()).rejects.toThrow(/returned 503/);
  });
});
