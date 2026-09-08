/**
 * What `getTranslations` serves when the API cannot be reached.
 *
 * `app/[locale]/layout.tsx` awaits this alongside `getActiveCategories` on
 * every request, and it used to throw unconditionally on a non-2xx. That was
 * right for `next build`'s prerender pass and wrong for a request already in
 * flight to a real visitor — a 504 mid-outage took the whole layout down with
 * it, which is one of the two throws behind a three-hour production outage
 * where `global-error.tsx` could not even render a page in place of it (see
 * the note there).
 *
 * So this must throw only during `next build`'s prerender pass
 * (`NEXT_PHASE === 'phase-production-build'`) and, at runtime, serve the last
 * translations that actually loaded in this isolate — or the repo-committed
 * seed if none ever did — instead of blanking the page.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import enSeed from './seed/en.json';
import arSeed from './seed/ar.json';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function loadServer(opts: { hasApi: boolean; isBuild: boolean }) {
  vi.resetModules();
  vi.stubEnv('NEXT_PUBLIC_API_URL', opts.hasApi ? 'https://api.example.com' : '');
  vi.stubEnv('NEXT_PHASE', opts.isBuild ? 'phase-production-build' : '');
  return import('./server');
}

describe('getTranslations', () => {
  it('serves the committed seed rather than throwing when the API is unreachable at runtime', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })));
    const { getTranslations } = await loadServer({ hasApi: true, isBuild: false });

    await expect(getTranslations('en')).resolves.toEqual(enSeed);
    await expect(getTranslations('ar')).resolves.toEqual(arSeed);
  });

  it('serves the seed when the fetch itself rejects at runtime', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('connection refused'); }));
    const { getTranslations } = await loadServer({ hasApi: true, isBuild: false });

    const translations = await getTranslations('en');
    expect(translations['nav.all']).toBe(enSeed['nav.all']);
  });

  it('serves the most recent successful fetch, not the stale seed, once one has landed', async () => {
    const fresh = { 'nav.all': 'Everything (fresh from the API)' };
    let up = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        up
          ? new Response(JSON.stringify(fresh), { status: 200 })
          : new Response('', { status: 503 }),
      ),
    );
    const { getTranslations } = await loadServer({ hasApi: true, isBuild: false });

    await expect(getTranslations('en')).resolves.toEqual(fresh);

    // The API now fails, but this isolate has already seen better data than
    // the committed seed — it should keep serving that, not fall all the way
    // back to the seed file.
    up = false;
    await expect(getTranslations('en')).resolves.toEqual(fresh);
  });

  it('still throws during the production build phase after exhausting retries, so a real outage fails the build loudly', async () => {
    const fetchMock = vi.fn(async () => new Response('', { status: 503 }));
    vi.stubGlobal('fetch', fetchMock);
    const { getTranslations } = await loadServer({ hasApi: true, isBuild: true });

    await expect(getTranslations('en')).rejects.toThrow(/translations/);
    // One attempt plus the retries — a persistent fault is not papered over.
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
  }, 15000);

  it('rides out a transient blip during the build: a 503 then a 200 resolves, not fails', async () => {
    const fresh = { hello: 'مرحبا' };
    let n = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        ++n === 1
          ? new Response('', { status: 503 })
          : new Response(JSON.stringify(fresh), { status: 200 }),
      ),
    );
    const { getTranslations } = await loadServer({ hasApi: true, isBuild: true });

    await expect(getTranslations('ar')).resolves.toEqual(fresh);
  }, 15000);

  it('returns {} when there is no API configured at all (CI), build or not', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));
    const { getTranslations } = await loadServer({ hasApi: false, isBuild: true });

    await expect(getTranslations('en')).resolves.toEqual({});
  });

  it('honours a genuine 200 carrying {}', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
    const { getTranslations } = await loadServer({ hasApi: true, isBuild: false });

    await expect(getTranslations('en')).resolves.toEqual({});
  });
});
