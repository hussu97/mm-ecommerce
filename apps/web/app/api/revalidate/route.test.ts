import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { NextRequest } from 'next/server';

/**
 * The API's purge endpoint. What matters: it revalidates exactly the tags it is
 * sent and only the storefront's own, only for the holder of the secret, and
 * it does not exist at all on a deployment that has no secret.
 */

const revalidateTag = vi.fn();
vi.mock('next/cache', () => ({ revalidateTag: (...args: unknown[]) => revalidateTag(...args) }));

const SECRET = 'a-shared-secret';

function request(body: unknown, auth: string | null = `Bearer ${SECRET}`) {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (auth !== null) headers.authorization = auth;
  return new Request('https://meltingmomentscakes.com/api/revalidate', {
    method: 'POST',
    headers,
    body: typeof body === 'string' ? body : JSON.stringify(body),
  }) as unknown as NextRequest;
}

async function post(req: NextRequest) {
  const { POST } = await import('./route');
  return POST(req);
}

describe('POST /api/revalidate', () => {
  beforeEach(() => {
    revalidateTag.mockReset();
    vi.stubEnv('STOREFRONT_REVALIDATE_SECRET', SECRET);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('revalidates each requested tag once, with stale-while-revalidate', async () => {
    const res = await post(request({ tags: ['catalogue', 'promo', 'catalogue'] }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ revalidated: ['catalogue', 'promo'] });
    expect(revalidateTag.mock.calls).toEqual([
      ['catalogue', 'max'],
      ['promo', 'max'],
    ]);
  });

  it('accepts every tag the storefront fetches with', async () => {
    const tags = ['i18n', 'cms', 'catalogue', 'promo', 'blog'];
    const res = await post(request({ tags }));
    expect(res.status).toBe(200);
    expect(revalidateTag).toHaveBeenCalledTimes(tags.length);
  });

  it('does not exist without a configured secret', async () => {
    vi.stubEnv('STOREFRONT_REVALIDATE_SECRET', '');
    const res = await post(request({ tags: ['catalogue'] }));
    expect(res.status).toBe(404);
    expect(revalidateTag).not.toHaveBeenCalled();
  });

  it.each([
    ['no header', null],
    ['a wrong secret', 'Bearer nope'],
    ['a secret of a different length', `Bearer ${SECRET}x`],
    ['the secret without the scheme', SECRET],
  ])('refuses %s', async (_label, auth) => {
    const res = await post(request({ tags: ['catalogue'] }, auth));
    expect(res.status).toBe(401);
    expect(revalidateTag).not.toHaveBeenCalled();
  });

  it.each([
    ['an unknown tag', { tags: ['catalogue', 'orders'] }],
    ['no tags', { tags: [] }],
    ['tags that are not a list', { tags: 'catalogue' }],
    ['a missing body field', {}],
    ['a body that is not JSON', 'not json'],
  ])('refuses %s without purging anything', async (_label, body) => {
    const res = await post(request(body));
    expect(res.status).toBe(400);
    expect(revalidateTag).not.toHaveBeenCalled();
  });
});
