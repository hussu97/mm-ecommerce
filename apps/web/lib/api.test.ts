import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest';
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
