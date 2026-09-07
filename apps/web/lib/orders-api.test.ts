import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { ordersApi } from './api';

// happy-dom's localStorage is flaky; the api layer touches it for the session id.
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
    key: (i: number) => Object.keys(store)[i] ?? null,
    get length() { return Object.keys(store).length; },
  };
})();

beforeEach(() => vi.stubGlobal('localStorage', localStorageMock));
afterEach(() => vi.restoreAllMocks());

function mockFetch(body: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('ordersApi.get ownership proof (F-ORD-20)', () => {
  it('sends the signed token, never the email, when a token is given', async () => {
    const fetchMock = mockFetch({});
    await ordersApi.get('MM-1', { token: 'tok_abc' });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('/orders/MM-1?');
    expect(url).toContain('token=tok_abc');
    expect(url).not.toContain('email=');
  });

  it('falls back to the email when only an email is given', async () => {
    const fetchMock = mockFetch({});
    await ordersApi.get('MM-1', { email: 'jane@example.com' });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('email=jane%40example.com');
  });

  it('sends no query at all when neither proof is given (a signed-in caller)', async () => {
    const fetchMock = mockFetch({});
    await ordersApi.get('MM-1');
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('/orders/MM-1');
    expect(url).not.toContain('?');
  });
});

describe('ordersApi.findByClientRequestId (F-WEB-5 recovery)', () => {
  it('looks the order up by its idempotency key and adopts it', async () => {
    const fetchMock = mockFetch({ items: [{ order_number: 'MM-42' }] });
    const found = await ordersApi.findByClientRequestId('cid-123', 'jane@example.com');
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('client_request_id=cid-123');
    expect(url).toContain('email=jane%40example.com');
    expect(found?.order_number).toBe('MM-42');
  });

  it('returns null when the attempt produced no order', async () => {
    mockFetch({ items: [] });
    const found = await ordersApi.findByClientRequestId('cid-123', 'jane@example.com');
    expect(found).toBeNull();
  });
});
