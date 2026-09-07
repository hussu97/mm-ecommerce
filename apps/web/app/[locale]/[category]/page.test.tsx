/**
 * `generateMetadata` must never throw a fetch failure into the SSR pipeline.
 *
 * `getCategoryMeta` throws on a 5xx/timeout by design (`fetchJsonOrNull` —
 * see `lib/fetch-json.ts`), so a blip is never ISR-cached as "this category
 * does not exist". That's the right contract for the page render, which has
 * an error boundary to land in — but `generateMetadata` runs before the page
 * gets that chance, so an uncaught throw here was a bare Vercel SSR crash
 * ("<category>: HTTP 500/504") instead of a rendered error page. This asserts
 * the metadata path swallows that failure and resolves to an empty metadata
 * object instead.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fetchJsonOrNull: vi.fn(),
  fetchJson: vi.fn(),
}));

vi.mock('@/lib/fetch-json', () => ({
  fetchJsonOrNull: mocks.fetchJsonOrNull,
  fetchJson: mocks.fetchJson,
}));

import { generateMetadata } from './page';

describe('category page generateMetadata', () => {
  beforeEach(() => {
    mocks.fetchJsonOrNull.mockReset();
    mocks.fetchJson.mockReset();
  });

  it('resolves to empty metadata rather than throwing when the category fetch 5xxs', async () => {
    mocks.fetchJsonOrNull.mockRejectedValue(new Error('GET /categories/cat-cookiemelt: HTTP 500'));

    await expect(
      generateMetadata({
        params: Promise.resolve({ locale: 'en', category: 'cat-cookiemelt' }),
      }),
    ).resolves.toEqual({});
  });

  it('resolves to empty metadata rather than throwing when the category fetch times out', async () => {
    mocks.fetchJsonOrNull.mockRejectedValue(new DOMException('The operation timed out.', 'TimeoutError'));

    await expect(
      generateMetadata({
        params: Promise.resolve({ locale: 'en', category: 'cat-cookiemelt' }),
      }),
    ).resolves.toEqual({});
  });

  it('still builds real metadata on the happy path', async () => {
    mocks.fetchJsonOrNull.mockResolvedValue({
      slug: 'cat-cookiemelt',
      name: 'Cookies',
      description: null,
      is_active: true,
      image_url: null,
    });

    const metadata = await generateMetadata({
      params: Promise.resolve({ locale: 'en', category: 'cat-cookiemelt' }),
    });

    expect(metadata.title).toBe('Cookies');
    expect(metadata.alternates?.canonical).toContain('/en/cat-cookiemelt');
  });

  it('resolves to empty metadata when the category genuinely does not exist', async () => {
    mocks.fetchJsonOrNull.mockResolvedValue(null);

    await expect(
      generateMetadata({
        params: Promise.resolve({ locale: 'en', category: 'nope' }),
      }),
    ).resolves.toEqual({});
  });
});
