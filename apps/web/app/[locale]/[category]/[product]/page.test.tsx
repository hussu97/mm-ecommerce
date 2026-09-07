/**
 * `generateMetadata` must never throw a fetch failure into the SSR pipeline.
 *
 * `getProduct` throws on a 5xx/timeout by design (`fetchJsonOrNull` — see
 * `lib/fetch-json.ts`): under ISR, a caught fetch failure that fell back to
 * "not found" would get cached as a 404 for the whole revalidate window, so a
 * real failure has to throw instead of degrading. That's the right contract
 * for the page render, which has an error boundary to land in — but
 * `generateMetadata` runs before the page gets that chance, so an uncaught
 * throw here was a bare Vercel SSR crash ("products/<slug>: HTTP 500/504")
 * instead of a rendered error page. This asserts the metadata path swallows
 * that failure and resolves to an empty metadata object instead.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fetchJsonOrNull: vi.fn(),
}));

vi.mock('@/lib/fetch-json', () => ({
  fetchJsonOrNull: mocks.fetchJsonOrNull,
  fetchJson: vi.fn(),
}));

import { generateMetadata } from './page';

describe('product page generateMetadata', () => {
  beforeEach(() => {
    mocks.fetchJsonOrNull.mockReset();
  });

  it('resolves to empty metadata rather than throwing when the product fetch 5xxs', async () => {
    mocks.fetchJsonOrNull.mockRejectedValue(
      new Error('GET /products/mix-cookies-box-of-9: HTTP 500'),
    );

    await expect(
      generateMetadata({
        params: Promise.resolve({
          locale: 'en',
          category: 'cat-cookiemelt',
          product: 'mix-cookies-box-of-9',
        }),
      }),
    ).resolves.toEqual({});
  });

  it('resolves to empty metadata rather than throwing when the product fetch times out', async () => {
    mocks.fetchJsonOrNull.mockRejectedValue(new DOMException('The operation timed out.', 'TimeoutError'));

    await expect(
      generateMetadata({
        params: Promise.resolve({
          locale: 'en',
          category: 'cat-cookiemelt',
          product: 'mix-cookies-box-of-9',
        }),
      }),
    ).resolves.toEqual({});
  });

  it('still builds real metadata on the happy path', async () => {
    mocks.fetchJsonOrNull.mockResolvedValue({
      slug: 'mix-cookies-box-of-9',
      name: 'Mix Cookies Box of 9',
      description: null,
      base_price: 45,
      created_at: '2026-01-01T00:00:00Z',
      is_active: true,
      image_urls: [],
      category: { slug: 'cat-cookiemelt', name: 'Cookies' },
    });

    const metadata = await generateMetadata({
      params: Promise.resolve({
        locale: 'en',
        category: 'cat-cookiemelt',
        product: 'mix-cookies-box-of-9',
      }),
    });

    expect(metadata.title).toBe('Mix Cookies Box of 9');
    expect(metadata.alternates?.canonical).toContain('/en/cat-cookiemelt/mix-cookies-box-of-9');
  });

  it('resolves to empty metadata when the product genuinely does not exist', async () => {
    mocks.fetchJsonOrNull.mockResolvedValue(null);

    await expect(
      generateMetadata({
        params: Promise.resolve({ locale: 'en', category: 'cat-cookiemelt', product: 'nope' }),
      }),
    ).resolves.toEqual({});
  });
});
