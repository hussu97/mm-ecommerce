import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest';
import { analytics } from './analytics';
import { formatPrice } from './utils';
import {
  ensureSessionId,
  getSessionId,
  clearSessionId,
  api,
  authApi,
  productsApi,
  ApiError,
  API_BASE,
} from './api';

// happy-dom may not fully implement all localStorage methods; provide a reliable mock.
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    key: (index: number) => Object.keys(store)[index] ?? null,
    get length() { return Object.keys(store).length; },
  };
})();

beforeAll(() => {
  vi.stubGlobal('localStorage', localStorageMock);
});

// ─── formatPrice edge cases ───────────────────────────────────────────────────

describe('formatPrice edge cases', () => {
  it('formats zero', () => {
    expect(formatPrice(0)).toBe('0.00 AED');
  });

  it('formats large numbers', () => {
    expect(formatPrice(1000000)).toBe('1000000.00 AED');
  });

  it('formats negative numbers', () => {
    expect(formatPrice(-10)).toBe('-10.00 AED');
  });

  it('formats string input', () => {
    expect(formatPrice('99.99')).toBe('99.99 AED');
  });

  it('formats number with rounding', () => {
    expect(formatPrice(1.456)).toBe('1.46 AED');
  });
});

// ─── ensureSessionId ──────────────────────────────────────────────────────────

describe('ensureSessionId', () => {
  beforeEach(() => clearSessionId());

  it('creates a new session ID if none exists', () => {
    const id = ensureSessionId();
    expect(id).toBeTruthy();
    expect(id).toMatch(/^sess_/);
  });

  it('persists the session ID in localStorage', () => {
    const id = ensureSessionId();
    expect(getSessionId()).toBe(id);
  });

  it('returns the same ID on subsequent calls', () => {
    const id1 = ensureSessionId();
    const id2 = ensureSessionId();
    expect(id1).toBe(id2);
  });
});

// ─── api fetch wrapper ────────────────────────────────────────────────────────

function makeMockFetch(status: number, body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
  });
}

describe('api', () => {
  beforeEach(() => {
    clearSessionId();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('api.get sends request to correct URL', async () => {
    const mockFetch = makeMockFetch(200, { data: 'test' });
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/products');

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${API_BASE}/products`);
  });

  it('api.get sends Content-Type header', async () => {
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/test');

    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers['Content-Type']).toBe('application/json');
  });

  it('api.get sends credentials: include', async () => {
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/test');

    const [, options] = mockFetch.mock.calls[0];
    expect(options.credentials).toBe('include');
  });

  it('api.get does not send Authorization header', async () => {
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/test');

    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers['Authorization']).toBeUndefined();
  });

  it('api.get includes X-Session-Id header when session exists', async () => {
    const sessionId = ensureSessionId();
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/test');

    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers['X-Session-Id']).toBe(sessionId);
  });

  it('api.post sends POST method with serialized body', async () => {
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.post('/test', { key: 'value' });

    const [, options] = mockFetch.mock.calls[0];
    expect(options.method).toBe('POST');
    expect(options.body).toBe(JSON.stringify({ key: 'value' }));
  });

  it('api.delete returns undefined for 204 No Content', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal('fetch', mockFetch);

    const result = await api.delete('/test');
    expect(result).toBeUndefined();
  });

  it('throws ApiError with correct message on non-ok response', async () => {
    const mockFetch = makeMockFetch(404, { detail: 'Not found' }, false);
    vi.stubGlobal('fetch', mockFetch);

    await expect(api.get('/test')).rejects.toThrow('Not found');
  });

  it('throws ApiError with status code on non-ok response', async () => {
    const mockFetch = makeMockFetch(404, { detail: 'Not found' }, false);
    vi.stubGlobal('fetch', mockFetch);

    let thrown: ApiError | undefined;
    try {
      await api.get('/test');
    } catch (e) {
      thrown = e as ApiError;
    }
    expect(thrown).toBeInstanceOf(ApiError);
    expect(thrown?.status).toBe(404);
  });

  it('throws session expired error on 401 when cookie refresh fails', async () => {
    const mockFetch = makeMockFetch(401, { detail: 'Unauthorized' }, false);
    vi.stubGlobal('fetch', mockFetch);

    await expect(api.get('/test')).rejects.toThrow('Session expired');
  });

  it('retries request after successful cookie-based token refresh on 401', async () => {
    const refreshResponse = {
      access_token: 'new-access-token',
      refresh_token: 'new-refresh-token',
      token_type: 'bearer',
      user: {},
    };

    const mockFetch = vi.fn()
      // First call: original request → 401
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: 'Unauthorized' }),
      })
      // Second call: refresh token request → 200 (sets new cookies)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(refreshResponse),
      })
      // Third call: retried original request → 200
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ data: 'success' }),
      });

    vi.stubGlobal('fetch', mockFetch);

    const result = await api.get('/protected');
    expect(result).toEqual({ data: 'success' });
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });
});

// ─── api_error reporting ─────────────────────────────────────────────────────

describe('api_error reporting', () => {
  beforeEach(() => {
    clearSessionId();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * This is the regression that matters most on this event.
   *
   * `/auth/me` runs on every page load to find out whether anybody is signed
   * in, and 401 is the correct answer for a shopper who is not — which is most
   * of this site's traffic. When `api_error` reported it, the event fired once
   * per anonymous page view: the loudest thing on the dashboard, saying only
   * "somebody visited while logged out", burying the 500s it exists to surface.
   */
  it('never reports a 401 — not being signed in is a state, not a failure', async () => {
    const spy = vi.spyOn(analytics, 'apiError');
    const mockFetch = makeMockFetch(401, { detail: 'Unauthorized' }, false);
    vi.stubGlobal('fetch', mockFetch);

    await expect(api.get('/auth/me')).rejects.toThrow('Session expired');

    expect(spy).not.toHaveBeenCalled();
  });

  it('reports the statuses that are real failures', async () => {
    const spy = vi.spyOn(analytics, 'apiError');
    const mockFetch = makeMockFetch(500, { detail: 'Boom' }, false);
    vi.stubGlobal('fetch', mockFetch);

    await expect(api.get('/delivery/quote')).rejects.toThrow('Boom');

    expect(spy).toHaveBeenCalledWith({
      status: 500,
      endpoint: '/delivery/quote',
      method: 'GET',
    });
  });

  it('reports a request that never arrived as status 0', async () => {
    const spy = vi.spyOn(analytics, 'apiError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(api.get('/cart')).rejects.toThrow('Failed to fetch');

    expect(spy).toHaveBeenCalledWith({ status: 0, endpoint: '/cart', method: 'GET' });
  });

  it('collapses identifiers so the endpoint stays groupable', async () => {
    const spy = vi.spyOn(analytics, 'apiError');
    const mockFetch = makeMockFetch(404, { detail: 'Not found' }, false);
    vi.stubGlobal('fetch', mockFetch);

    await expect(api.get('/orders/MM-20260808-0042')).rejects.toThrow('Not found');

    expect(spy).toHaveBeenCalledWith({
      status: 404,
      endpoint: '/orders/:orderNumber',
      method: 'GET',
    });
  });
});

// ─── authApi ──────────────────────────────────────────────────────────────────

describe('authApi', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('authApi.login calls /auth/login with correct credentials', async () => {
    const tokenData = {
      access_token: 'acc-token',
      refresh_token: 'ref-token',
      token_type: 'bearer',
      user: { id: '1', email: 'test@example.com' },
    };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(tokenData),
    });
    vi.stubGlobal('fetch', mockFetch);

    await authApi.login('test@example.com', 'password123');

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/auth/login');
    expect(JSON.parse(options.body as string)).toEqual({
      email: 'test@example.com',
      password: 'password123',
    });
    // Tokens are now managed by httpOnly cookies, not localStorage
    expect(options.credentials).toBe('include');
  });
});

// ─── productsApi ─────────────────────────────────────────────────────────────

describe('productsApi', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('productsApi.list calls /products', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ items: [], total: 0, page: 1, per_page: 20, pages: 1 }),
    });
    vi.stubGlobal('fetch', mockFetch);

    await productsApi.list();

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/products');
  });

  it('productsApi.list passes category param as query string', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ items: [], total: 0, page: 1, per_page: 20, pages: 1 }),
    });
    vi.stubGlobal('fetch', mockFetch);

    await productsApi.list({ category: 'cakes' });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('category=cakes');
  });

  it('productsApi.bySlug calls correct endpoint', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    });
    vi.stubGlobal('fetch', mockFetch);

    await productsApi.bySlug('chocolate-cake');

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/products/chocolate-cake');
  });
});

// ─── The guest basket, in browsers that make it hard ──────────────────────────
//
// Every one of these is a browser a real customer of this shop arrives in. The
// storefront's traffic is overwhelmingly the Instagram in-app browser on iOS,
// which evicts `localStorage` mid-session; Safari with "Block All Cookies" set
// makes it throw outright; and an iPhone left on iOS 15.3 has no
// `crypto.randomUUID` at all.
//
// The rule these assert is one rule: **a guest always ends up with an id, and
// with the same id for as long as the page lives.** Without it `request()`
// sends no `X-Session-Id`, and an API that cannot tell whose basket it is being
// asked about is how four customers' items ended up in one cart.

describe('guest session id in a hostile browser', () => {
  const throwingStorage = {
    getItem: () => {
      throw new Error('The operation is insecure.');
    },
    setItem: () => {
      throw new Error('The operation is insecure.');
    },
    removeItem: () => {
      throw new Error('The operation is insecure.');
    },
    clear: () => {},
    key: () => null,
    length: 0,
  };

  beforeEach(() => {
    vi.stubGlobal('localStorage', localStorageMock);
    clearSessionId();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal('localStorage', localStorageMock);
  });

  it('sends X-Session-Id even when nothing was ever stored', async () => {
    // The regression. `request()` used to *read* the id, so a shopper whose
    // storage had never been seeded — or had been evicted since — sent no
    // header at all, and the API filed their item in a cart owned by nobody.
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/test');

    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers['X-Session-Id']).toBeTruthy();
  });

  it('keeps one id across requests when localStorage throws', async () => {
    vi.stubGlobal('localStorage', throwingStorage);
    const mockFetch = makeMockFetch(200, {});
    vi.stubGlobal('fetch', mockFetch);

    await api.get('/one');
    await api.get('/two');

    const first = mockFetch.mock.calls[0][1].headers['X-Session-Id'];
    const second = mockFetch.mock.calls[1][1].headers['X-Session-Id'];
    expect(first).toBeTruthy();
    // Two carts for one shopper in one visit is the failure this prevents.
    expect(second).toBe(first);
  });

  it('does not throw when storage refuses to persist', () => {
    vi.stubGlobal('localStorage', throwingStorage);
    expect(() => ensureSessionId()).not.toThrow();
    expect(ensureSessionId()).toBeTruthy();
  });

  it('mints an id without crypto.randomUUID', () => {
    // iOS below 15.4. The bare call throws there, and because `request()` is
    // the funnel for every endpoint, an unguarded throw would take the product
    // grid down with the basket rather than just the basket.
    vi.stubGlobal('crypto', { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) });
    const id = ensureSessionId();
    expect(id).toMatch(/^sess_[0-9a-f]{32}$/);
  });

  it('mints a unique id with no usable crypto at all', () => {
    vi.stubGlobal('crypto', {});

    const first = ensureSessionId();
    clearSessionId();
    const second = ensureSessionId();

    expect(first).toBeTruthy();
    expect(second).toBeTruthy();
    // A collision here would be the shared-basket bug all over again.
    expect(second).not.toBe(first);
  });

  it('prefers the stored id over the in-memory one', () => {
    // The durable copy stays authoritative, so a reload keeps the basket.
    const minted = ensureSessionId();
    expect(getSessionId()).toBe(minted);
    expect(localStorage.getItem('mm_session_id')).toBe(minted);
  });
});
