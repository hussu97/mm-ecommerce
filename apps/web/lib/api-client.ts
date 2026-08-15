/**
 * The browser half of the storefront's API layer.
 *
 * Everything in here rides on `request()` — one function that carries the
 * session header, the cookie credentials, the 401-refresh-retry and the
 * `api_error` analytics for every endpoint the storefront has. If a call needs
 * to leave this file's guarantees behind, that is a design decision and gets a
 * comment (see `refreshAccessToken`), not a quiet second `fetch`.
 *
 * Server-side code — Server Components, route handlers, `sitemap.ts` — must
 * not import this module (the `client-only` marker below turns that mistake
 * into a build error); it uses `lib/api-server.ts` instead, whose base URL is
 * absolute for reasons documented there.
 */
import 'client-only';

import { analytics, normalisePath } from './analytics';
import { AdvertisedPromo, Cart, Product, ProductListResponse, TokenResponse, User, PromoValidateResponse, Order, Address, AddressCreate, OrderCreate, PaymentSessionResponse, PaymentMethod, toWireMethod, DeliveryRates, DeliveryQuote, DeliveryArea, PickupBranch, TrackResult } from './types';
import { API_BASE } from './api-base';

export { API_BASE };

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

/**
 * A raw `fetch`, deliberately — the one bypass of `request()` in this file.
 *
 * This is the retry primitive `request()` itself reaches for on a 401: routed
 * through `request()` it would recurse into its own refresh on failure, and
 * reporting its 401s to analytics would re-create the noise the wrapper's
 * comment warns about. It stays a bare call so the rest of the file can afford
 * not to be.
 */
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
  /** The small extras the basket offers alongside itself. */
  cartAddons: (limit = 8) => api.get<Product[]>(`/products/cart-addons?limit=${limit}`),
  bySlug: (slug: string) => api.get<Product>(`/products/${slug}`),
};

export const cartApi = {
  get: () => api.get<Cart>('/cart'),
  addItem: (
    product_id: string,
    quantity: number,
    selected_options: Array<{modifier_id: string; option_id: string}> = [],
    personalisation_note?: string,
  ) =>
    api.post<Cart>('/cart/items', { product_id, quantity, selected_options, personalisation_note }),
  updateItem: (item_id: string, quantity: number) =>
    api.put<Cart>(`/cart/items/${item_id}`, { quantity }),
  /**
   * Set the message on a line.
   *
   * Separate from `updateItem` because this one is called as the customer
   * types. Sending a quantity alongside a debounced note would eventually put
   * back the quantity that was on screen when they started typing.
   */
  updateItemNote: (item_id: string, note: string) =>
    api.put<Cart>(`/cart/items/${item_id}/note`, { note }),
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
    identity?: {
      email?: string | null;
      phone?: string | null;
      /**
       * Which kind of order this will be, once that is known. The phone gate is
       * asked of deliveries and not of collections, so a basket that has not
       * chosen yet sends nothing and gets the cautious answer.
       */
      delivery_method?: 'delivery' | 'pickup' | null;
    },
  ) =>
    api.post<PromoValidateResponse>('/promo-codes/validate', {
      code,
      order_subtotal,
      email: identity?.email || null,
      phone: identity?.phone || null,
      delivery_method: identity?.delivery_method || null,
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
  /**
   * Ask the server to start a payment.
   *
   * `method` is what the customer chose — `card` or `cod`. It is deliberately
   * not a gateway: which processor settles a card is decided server-side from
   * the `payment_gateways` table, so a Stripe outage is answered by an admin
   * toggle rather than a release, and no client can pick its own processor.
   *
   * Both field names go on the wire for the duration of one rollout, and the
   * legacy one carries the legacy *word*. The web and the API deploy in
   * parallel and the API is the slower of the two, so this bundle runs against
   * the previous API for minutes — and that one resolves a provider by exact
   * string, where `card` is not one. See `toWireMethod`.
   */
  createSession: (orderNumber: string, method: PaymentMethod) =>
    api.post<PaymentSessionResponse>('/payments/create-session', {
      order_number: orderNumber,
      method,
      provider: toWireMethod(method),
    }),
};

export const trackApi = {
  /**
   * Find an order by its number and the email on it.
   *
   * This was a raw `fetch` that skipped everything `request()` promises, so a
   * failing track endpoint was invisible on the dashboard. Nothing about it
   * needed the bypass: the endpoint is public, and the session header is
   * ignored there. Routed through `request()`, a lookup that fails for a real
   * reason now shows up in `api_error` like every other endpoint.
   */
  lookup: (order_number: string, email: string) =>
    api.post<TrackResult>('/orders/track', { order_number, email }),
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
