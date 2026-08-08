import { analytics, normalisePath } from './analytics';
import { AdvertisedPromo, Cart, Product, ProductListResponse, TokenResponse, User, PromoValidateResponse, Order, Address, AddressCreate, OrderCreate, PaymentSessionResponse, DeliveryRates, DeliveryQuote, DeliveryArea, PickupBranch, TrackResult } from './types';
import { CACHE_TAGS, CONTENT_TTL } from './cache-policy';

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

  const method = (options.method ?? 'GET').toUpperCase();

  /**
   * Record the failure once, here, for every endpoint the storefront has.
   *
   * Thirty call sites each catch their own errors and show their own toast, and
   * none of them told us anything — so a 500 on the delivery quote and a 429 on
   * the promo check both looked, from the dashboard, exactly like a quiet day.
   * The one place every one of them passes through is this function.
   *
   * **401 is not a failure and is never recorded.** `/auth/me` runs on every
   * page load to find out whether anybody is signed in, and for a shopper who
   * is not — which is the overwhelming majority of this site's traffic — the
   * honest answer to that question is 401. Reporting it made `api_error` fire
   * once per anonymous page view: the loudest event on the site, saying only
   * "somebody visited while logged out", drowning the 500s this exists to
   * surface and spending the event quota on it. That is exactly what happened
   * for the twenty minutes after this shipped.
   *
   * A session that dies mid-journey is still visible, because whatever the
   * customer was doing when it died fails its own way and has its own event.
   */
  const report = (status: number) => {
    if (status === 401) return;
    analytics.apiError({ status, endpoint: normalisePath(path), method });
  };

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'include' }).catch(
    (err) => {
      // A request that never arrived. Status 0 is the convention for it, and
      // distinguishing it matters: it is the shape a blocked or dropped mobile
      // connection takes, not a server that answered badly.
      report(0);
      throw err;
    },
  );

  if (res.status === 401 && _retry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, options, false);
    }
    throw new ApiError(401, 'Session expired. Please log in again.');
  }

  if (!res.ok) {
    report(res.status);
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
  /**
   * Record a completed phone verification.
   *
   * The token comes from Firebase; the server checks its signature and audience
   * and returns the number *it* read, which is the one to keep. Trusting the
   * number the browser typed would make the whole exchange decorative.
   */
  /**
   * Whether this number has already been proved recently enough to count.
   *
   * A proof belongs to the number, not to the address it was first entered on —
   * so somebody who verified saving a home address and then adds a work one is
   * not asked again. The server has always known this; nothing asked it.
   */
  phoneVerified: (phone: string) =>
    api.get<{ phone: string; verified: boolean }>(
      `/auth/phone-verified?phone=${encodeURIComponent(phone)}`,
    ),

  verifyPhone: (firebaseIdToken: string, turnstileToken?: string | null) =>
    api.post<{ phone: string; verified: boolean }>('/auth/verify-phone', {
      firebase_id_token: firebaseIdToken,
      turnstile_token: turnstileToken ?? null,
    }),

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
  /**
   * Whether this code applies to this basket, for this customer.
   *
   * `identity` is not optional decoration. A new-customer coupon is refused on
   * an account, an email or a phone that has ordered before, and order creation
   * checks all three — so validating without them answers a different question
   * from the one the checkout is about to be judged on. The discount shows as
   * applied, the customer reaches the pay button, and the order is refused
   * there. Send whatever the form knows; the server decides.
   */
  validate: (
    code: string,
    order_subtotal: number,
    identity?: { email?: string | null; phone?: string | null },
  ) =>
    api.post<PromoValidateResponse>('/promo-codes/validate', {
      code,
      order_subtotal,
      email: identity?.email || null,
      phone: identity?.phone || null,
    }),
  /**
   * The new-customer coupon currently being advertised, or `null` when none is.
   *
   * Every figure the storefront prints about the offer comes from here rather
   * than from a constant, so changing the coupon in the admin changes the tray,
   * its terms and the code it applies together. Null is an ordinary answer —
   * "no campaign running" — and renders as no tray, not as an error.
   */
  featured: () => api.get<AdvertisedPromo | null>('/promo-codes/featured'),
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
   * What delivery looks like at a point, before there is a basket.
   *
   * Distinct from `quote` and much cheaper: a point-in-polygon lookup with no
   * courier call behind it, so it is safe on a homepage. Use it for anything
   * that describes delivery; use `quote` only when there is a real cart to
   * price.
   */
  area: (latitude: number, longitude: number) =>
    api.get<DeliveryArea>(
      `/delivery/area?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`,
    ),
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
   * Page content for one slug and locale.
   *
   * This was `cache: 'no-store'`, and it earned it: the 049 content migration
   * writes straight to Postgres, the Vercel build ran while the API was still
   * answering from its pre-migration Redis copy, and the stale answer stuck in
   * the data cache — one locale shipped the new home page and the other kept
   * serving the old one long after both the database and the API agreed on the
   * new content.
   *
   * Note what actually made that bad. Not that a cache existed, but that the
   * entry never expired on its own, so it outlived the disagreement that
   * created it and had to be found by a human. `CONTENT_TTL` is a minute; the
   * same mistake now corrects itself before anyone can report it, and the
   * `cms` tag is there for the on-demand purge that would make it instant.
   *
   * What the old comment got wrong is the cost side: "every page that reads the
   * CMS is already dynamic" was true, and was the problem rather than the
   * justification — `no-store` is *why* they were dynamic. The home page, the
   * about page, the FAQ and the privacy page have no per-visitor content in
   * them at all, and were being rendered from scratch for every visit to fetch
   * copy that changes a few times a year.
   *
   * Still throws rather than falling back: callers each have their own baked-in
   * copy to degrade to, and a thrown fetch is not written to the cache, so a
   * failed revalidation keeps serving the last good answer instead of caching
   * an empty one.
   */
  getPage: (slug: string, locale: string): Promise<{ slug: string; content: Record<string, unknown> }> => {
    return fetch(`${RSC_API_BASE}/cms/public/${slug}?locale=${locale}`, {
      next: { revalidate: CONTENT_TTL, tags: [CACHE_TAGS.cms] },
      signal: AbortSignal.timeout(8000),
    })
      .then(res => {
        if (!res.ok) throw new Error(`CMS fetch failed: ${res.status}`);
        return res.json();
      });
  },
};
