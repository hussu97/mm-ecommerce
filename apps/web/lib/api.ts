import { Cart, Product, ProductListResponse, TokenResponse, User, PromoValidateResponse, Order, Address, AddressCreate, OrderCreate, PaymentSessionResponse, DeliveryRates, DeliveryQuote, PickupBranch, TrackResult } from './types';

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/v1';

/**
 * Base URL for fetches that run on the server — Server Components, route
 * handlers, `sitemap.ts`, `generateMetadata`.
 *
 * `API_BASE` is a relative path in dev so the browser goes through the Next
 * rewrite and cookies stay same-origin, but Node's fetch cannot resolve a
 * relative URL with no request to resolve it against. Worse than throwing: in
 * a static prerender it never settles, so a `try`/`catch` fallback around it
 * never runs and the build worker is killed at its 60s timeout. Production
 * sets an absolute `NEXT_PUBLIC_API_URL`, which is why this only ever bites
 * locally. Every server-side fetch must use this, never `API_BASE`.
 */
export const RSC_API_BASE = API_BASE.startsWith('http')
  ? API_BASE
  : (process.env.NEXT_PRIVATE_API_HOST ?? 'http://localhost:8000') + '/api/v1';

// ─── Error ────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ─── Session helpers ──────────────────────────────────────────────────────────

export function getSessionId(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('mm_session_id');
}

export function ensureSessionId(): string {
  let id = getSessionId();
  if (!id) {
    id = 'sess_' + crypto.randomUUID().replace(/-/g, '');
    localStorage.setItem('mm_session_id', id);
  }
  return id;
}

export function clearSessionId(): void {
  localStorage.removeItem('mm_session_id');
}

// ─── Refresh access token ─────────────────────────────────────────────────────

async function refreshAccessToken(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({}),
  });
  return res.ok;
}

// ─── Core fetch ───────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}, _retry = true): Promise<T> {
  const sessionId = getSessionId();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (sessionId) headers['X-Session-Id'] = sessionId;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'include' });

  if (res.status === 401 && _retry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, options, false);
    }
    throw new ApiError(401, 'Session expired. Please log in again.');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    // Pydantic 422 returns detail as an array of {loc, msg, type}
    let message: string;
    if (Array.isArray(body.detail)) {
      message = body.detail.map((e: { msg: string }) => e.msg).join('; ');
    } else {
      message = body.detail || `HTTP ${res.status}`;
    }
    throw new ApiError(res.status, message);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ─── HTTP methods ─────────────────────────────────────────────────────────────

export const api = {
  get:    <T>(path: string)                  => request<T>(path),
  post:   <T>(path: string, data?: unknown)  => request<T>(path, { method: 'POST',   body: JSON.stringify(data) }),
  put:    <T>(path: string, data?: unknown)  => request<T>(path, { method: 'PUT',    body: JSON.stringify(data) }),
  patch:  <T>(path: string, data?: unknown)  => request<T>(path, { method: 'PATCH',  body: JSON.stringify(data) }),
  delete: <T>(path: string)                  => request<T>(path, { method: 'DELETE' }),
};

// ─── Typed endpoints ──────────────────────────────────────────────────────────

/**
 * The language this page is being read in, off the URL.
 *
 * Every storefront route is `/{locale}/…`, so the path is the one source that
 * is always right and always available — including in a module with no React
 * context to read from. Defaulted into the two calls that trigger an email, so
 * a caller cannot forget to pass it and quietly send an Arabic customer an
 * English password reset.
 */
export function currentLocale(): 'en' | 'ar' {
  if (typeof window === 'undefined') return 'en';
  return window.location.pathname.split('/')[1] === 'ar' ? 'ar' : 'en';
}

export const authApi = {
  register: (data: {
    email: string;
    password: string;
    phone?: string;
    /** Turnstile solution. Omitted where the site key is unset, which the API treats as "check disabled". */
    turnstile_token?: string;
  }) => api.post<TokenResponse>('/auth/register', { locale: currentLocale(), ...data }),
  login: (email: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { email, password }),
  guest: (email?: string) =>
    api.post<TokenResponse>('/auth/guest', { email }),
  refresh: () => api.post<TokenResponse>('/auth/refresh', {}),
  logout: () => api.post<void>('/auth/logout', {}).catch(() => {}),
  me: () => api.get<User>('/auth/me'),
  updateMe: (data: { phone?: string }) =>
    api.put<User>('/auth/me', data),
  forgotPassword: (email: string, turnstile_token?: string) =>
    api.post<{ message: string }>('/auth/forgot-password', {
      email,
      locale: currentLocale(),
      turnstile_token,
    }),
  resetPassword: (token: string, new_password: string) =>
    api.post<{ message: string }>('/auth/reset-password', { token, new_password }),
};

export const productsApi = {
  list: (params?: { category?: string; search?: string; featured?: boolean; sort?: string; page?: number; per_page?: number }) => {
    const qs = params ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString() : '';
    return api.get<ProductListResponse>(`/products${qs}`);
  },
  featured: (limit = 8) => api.get<Product[]>(`/products/featured?limit=${limit}`),
  bySlug: (slug: string) => api.get<Product>(`/products/${slug}`),
};

export const cartApi = {
  get: () => api.get<Cart>('/cart'),
  addItem: (product_id: string, quantity: number, selected_options: Array<{modifier_id: string; option_id: string}> = []) =>
    api.post<Cart>('/cart/items', { product_id, quantity, selected_options }),
  updateItem: (item_id: string, quantity: number) =>
    api.put<Cart>(`/cart/items/${item_id}`, { quantity }),
  removeItem: (item_id: string) =>
    api.delete<Cart>(`/cart/items/${item_id}`),
  clear: () => api.delete<Cart>('/cart'),
  merge: (session_id: string) =>
    api.post<Cart>('/cart/merge', { session_id }),
};

export const promoApi = {
  validate: (code: string, order_subtotal: number) =>
    api.post<PromoValidateResponse>('/promo-codes/validate', { code, order_subtotal }),
};

export const addressesApi = {
  list: () => api.get<Address[]>('/addresses'),
  create: (data: AddressCreate) => api.post<Address>('/addresses', data),
  update: (id: string, data: Partial<AddressCreate>) => api.put<Address>(`/addresses/${id}`, data),
  delete: (id: string) => api.delete<void>(`/addresses/${id}`),
  setDefault: (id: string) => api.put<Address>(`/addresses/${id}/default`),
};

export const ordersApi = {
  create: (data: OrderCreate) => api.post<Order>('/orders', data),
  list: (page = 1) =>
    api.get<{ items: Order[]; total: number; page: number; pages: number }>(`/orders?page=${page}`),
  get: (orderNumber: string, email?: string) =>
    api.get<Order>(`/orders/${orderNumber}${email ? `?email=${encodeURIComponent(email)}` : ''}`),
};

export const branchesApi = {
  /**
   * The branches a customer may collect from. Public — a guest choosing pickup
   * has to see the same list a signed-in customer does.
   */
  pickupPoints: () => api.get<PickupBranch[]>('/branches/pickup-points'),
};

export const paymentsApi = {
  createSession: (orderNumber: string, provider: string) =>
    api.post<PaymentSessionResponse>('/payments/create-session', {
      order_number: orderNumber,
      provider,
    }),
};

export const trackApi = {
  lookup: async (order_number: string, email: string): Promise<TrackResult> => {
    const res = await fetch(`${API_BASE}/orders/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ order_number, email }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: 'Order not found.' }));
      throw new ApiError(res.status, body.detail);
    }
    return res.json() as Promise<TrackResult>;
  },
};

export const deliveryApi = {
  getRates: () => api.get<DeliveryRates>('/delivery/rates'),
  /**
   * What delivery costs to a specific point. Priced against the active zone
   * map, so the figure on screen is the one the order gets written with.
   *
   * `address` is the pin's formatted address. It is not used to price anything
   * — the pin already did that — it travels so the server can record what the
   * same trip would cost to fulfil, against the same place a driver would be
   * sent to.
   */
  quote: (
    subtotal: number,
    latitude: number | null,
    longitude: number | null,
    address?: string | null,
  ) =>
    api.post<DeliveryQuote>('/delivery/quote', {
      subtotal,
      latitude,
      longitude,
      address: address || null,
    }),
};

export const cmsApi = {
  /**
   * Page content, read fresh on every render.
   *
   * This deliberately opts out of the Next data cache. The API already caches
   * each `slug`/`locale` in Redis for five minutes and drops that key the
   * moment the admin saves, so a second five-minute cache in front of it buys
   * nothing and adds a layer nobody can see into or clear. It has already cost
   * us once: the 049 content migration writes straight to Postgres, the Vercel
   * build ran while the API was still answering from its pre-migration Redis
   * copy, and the stale answer stuck in the data cache — one locale shipped the
   * new home page and the other kept serving the old one long after both the
   * database and the API agreed on the new content.
   *
   * Every page that reads the CMS is already dynamic, so the cost is one
   * intra-request call to an endpoint that answers from memory.
   */
  getPage: (slug: string, locale: string): Promise<{ slug: string; content: Record<string, unknown> }> => {
    return fetch(`${RSC_API_BASE}/cms/public/${slug}?locale=${locale}`, { cache: 'no-store', signal: AbortSignal.timeout(8000) })
      .then(res => {
        if (!res.ok) throw new Error(`CMS fetch failed: ${res.status}`);
        return res.json();
      });
  },
};
