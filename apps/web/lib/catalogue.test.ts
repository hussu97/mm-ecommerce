/**
 * What `getCategories` does when the API cannot be reached.
 *
 * `app/[locale]/layout.tsx` awaits this alongside `getTranslations` on every
 * request. Throwing unconditionally on a non-2xx was right for `next build`'s
 * prerender pass — a build that cannot reach the API should fail rather than
 * ship a page with no navigation baked in — and wrong for a request already
 * in flight to a real visitor: a category fetch that 504s mid-outage used to
 * take the whole layout down with it, which is one of the two throws behind
 * the three-hour outage `global-error.tsx` could not even render a page for.
 *
 * So this must throw only during `next build`'s prerender pass
 * (`NEXT_PHASE === 'phase-production-build'`) and return `[]` for every other
 * failure, with an API configured or not.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function loadCatalogue(opts: { hasApi: boolean; isBuild: boolean }) {
  vi.resetModules();
  vi.stubEnv('NEXT_PUBLIC_API_URL', opts.hasApi ? 'https://api.example.com' : '');
  vi.stubEnv('NEXT_PHASE', opts.isBuild ? 'phase-production-build' : '');
  return import('./catalogue');
}

describe('getCategories', () => {
  it('returns [] rather than throwing when the API 5xxs at runtime', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })));
    const { getCategories } = await loadCatalogue({ hasApi: true, isBuild: false });

    await expect(getCategories()).resolves.toEqual([]);
  });

  it('returns [] rather than throwing when the fetch itself rejects at runtime', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('connection refused'); }));
    const { getCategories } = await loadCatalogue({ hasApi: true, isBuild: false });

    await expect(getCategories()).resolves.toEqual([]);
  });

  it('still throws during the production build phase, so the build fails loudly', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })));
    const { getCategories } = await loadCatalogue({ hasApi: true, isBuild: true });

    await expect(getCategories()).rejects.toThrow(/categories/);
  });

  it('returns [] when there is no API configured at all (CI), build or not', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));
    const noApiBuild = await loadCatalogue({ hasApi: false, isBuild: true });
    await expect(noApiBuild.getCategories()).resolves.toEqual([]);
  });

  it('honours a real 200 carrying an empty list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })));
    const { getCategories } = await loadCatalogue({ hasApi: true, isBuild: false });

    await expect(getCategories()).resolves.toEqual([]);
  });
});
