/**
 * The sitemap describes the catalogue as it is now, and every product page in
 * it, sold out or not.
 *
 * On 2026-09-27 it still listed three sold-out cakes five hours after they sold
 * out. Those pages were 404s at the time. The cause was two layers of
 * stale-while-revalidate (route ISR, then the fetch data cache), each moved
 * only by sparse crawler requests. These tests check the three things that
 * fixed it: the route renders per request, every fetch skips the data cache,
 * and the product list asks for sold-out pages too.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import sitemap, { dynamic } from './sitemap';

const json = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

function mockApi() {
  const calls: { url: string; init?: RequestInit & { next?: unknown } }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes('/categories')) {
        return json([{ slug: 'desserts', is_active: true, updated_at: '2026-09-27T00:00:00Z' }]);
      }
      if (url.includes('/products')) {
        return json({
          items: [
            {
              slug: 'tiramisu',
              updated_at: '2026-09-27T00:00:00Z',
              category: { slug: 'desserts' },
              is_available: false,
            },
          ],
          pages: 1,
        });
      }
      return json({ items: [], pages: 1 });
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('sitemap', () => {
  it('is rendered per request, not held in ISR', () => {
    expect(dynamic).toBe('force-dynamic');
  });

  it('reads live data on every fetch', async () => {
    const calls = mockApi();
    await sitemap();

    expect(calls.length).toBeGreaterThan(0);
    for (const { url, init } of calls) {
      expect(init?.cache, url).toBe('no-store');
      expect(init?.next, url).toBeUndefined();
    }
  });

  it('lists a sold-out product, whose page is live', async () => {
    const calls = mockApi();
    const entries = await sitemap();

    const productCall = calls.find((c) => c.url.includes('/products'));
    expect(productCall?.url).toContain('include_unavailable=true');
    expect(entries.map((e) => e.url)).toContain(
      'https://meltingmomentscakes.com/en/desserts/tiramisu',
    );
  });
});
