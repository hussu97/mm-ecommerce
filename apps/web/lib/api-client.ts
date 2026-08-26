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
import { readBranch } from '@/lib/location/branch-cookie';
import 'client-only';

import { analytics, normalisePath } from './analytics';
import { AdvertisedPromo, Cart, Product, ProductListResponse, TokenResponse, User, PromoValidateResponse, Order, Address, AddressCreate, OrderCreate, PaymentSessionResponse, PaymentMethod, toWireMethod, DeliveryRates, DeliveryQuote, DeliveryArea, OrderPreview, PickupBranch, TrackResult, ApplePayEligibility, ApplePayIntent } from './types';
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

/**
 * The guest session id, held in memory as well as in `localStorage`.
 *
 * `localStorage` is the durable copy and stays the one that matters — it is
 * what lets a basket survive a reload. The in-memory copy exists because it can
 * go away underneath us: the Instagram in-app browser evicts storage
 * mid-session, and some privacy modes make `setItem` throw outright. Either way
 * the old code answered `null`, `request()` sent no `X-Session-Id`, and the API
 * had no idea whose basket it was being asked about — which is how four
 * customers' items ended up in one shared cart (see migration 117).
 *
 * A tab-lifetime id is not as good as a persisted one. It is enormously better
 * than none: the shopper keeps their own basket for as long as they are
 * shopping, instead of being handed a stranger's.
 */
let memorySessionId: string | null = null;

function storedSessionId(): string | null {
  try {
    return localStorage.getItem('mm_session_id');
  } catch {
    // Storage disabled by the browser, not merely empty. Not an error state
    // worth reporting — it is a setting, and the fallback below handles it.
    return null;
  }
}

export function getSessionId(): string | null {
  if (typeof window === 'undefined') return null;
  return storedSessionId() ?? memorySessionId;
}

/**
 * A new guest session id, on a browser that may not have the good API.
 *
 * `crypto.randomUUID` is the right answer and is what this returns whenever it
 * exists. It is not, however, something to call unguarded from `request()`:
 * it needs a secure context and Safari only got it in 15.4, so on an older
 * iPhone the bare call throws — and since `request()` is the funnel for
 * *every* endpoint the storefront has, that would take the product grid and
 * the translations down with the basket. The old code got away with calling it
 * unguarded because it only ran from `CartProvider`, where a throw cost the
 * cart and nothing else.
 *
 * Each fallback is narrower than the one above it and all of them are wide
 * enough that two shoppers do not collide, which is the only property that
 * actually matters here — a collision is the shared-basket bug all over again.
 */
function newSessionId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return 'sess_' + crypto.randomUUID().replace(/-/g, '');
    }
  } catch {
    // Present but refusing — a non-secure context. Try the older API.
  }

  try {
    if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      return (
        'sess_' +
        Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
      );
    }
  } catch {
    // No usable crypto at all. Below is not a security boundary — a session id
    // is not a credential, it only has to be unlikely to repeat.
  }

  const rand = () => Math.random().toString(36).slice(2, 10);
  return `sess_${Date.now().toString(36)}${rand()}${rand()}${rand()}`;
}

/**
 * Never throws. `request()` calls this on every request the storefront makes,
 * so a throw here is the whole site rather than one basket.
 */
export function ensureSessionId(): string {
  const id = getSessionId() || newSessionId();
  memorySessionId = id;
  try {
    localStorage.setItem('mm_session_id', id);
  } catch {
    // Kept in memory above, so this page's basket is still coherent.
  }
  return id;
}

export function clearSessionId(): void {
  memorySessionId = null;
  try {
    localStorage.removeItem('mm_session_id');
  } catch {
    // Nothing was stored, so there is nothing to retire.
  }
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
  /**
   * `ensure`, not `get`. Reading it meant every call site inherited whatever
   * `localStorage` happened to hold at that moment, and only `CartProvider`'s
   * mount and `refreshCart` ever put anything there. A shopper whose storage
   * was evicted after mount then had `addItem` and `updateItem` go out with no
   * identity at all while reads quietly re-minted one — which is exactly the
   * asymmetry seen in production, where `GET /cart` was 49-for-49 and the
   * quantity stepper answered 400.
   *
   * Minting it here costs nothing when one already exists and means no request
   * in this file can be the one that forgets.
   */
  const sessionId = ensureSessionId();

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
  /**
   * The cart's add-on tray, for the kitchen this shopper's pin resolves to.
   *
   * Client-side, so it reads the cookie rather than being handed a branch: the
   * tray is a suggestion the customer did not ask for, and a suggestion their
   * own branch cannot make is worse than no tray at all.
   */
  cartAddons: (limit = 8) => {
    const branch = readBranch();
    return api.get<Product[]>(
      `/products/cart-addons?limit=${limit}${branch ? `&branch_id=${encodeURIComponent(branch)}` : ''}`,
    );
  },
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
  /**
   * Remember which code the basket has applied, or `null` to forget it.
   *
   * The code only — never the discount. What it is worth depends on the
   * basket, the identity and the day, and the checkout re-validates rather
   * than trusting a number carried from another screen.
   *
   * This is what makes the discount survive the hop to the checkout. It used
   * to travel in `sessionStorage` alone, and when that write failed the
   * checkout priced the order at full price *and submitted it that way* —
   * both the preview and the order gate on a discount being present.
   */
  setPromo: (code: string | null) =>
    api.put<Cart>('/cart/promo', { code }),
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
  /**
   * The exact totals an order placed right now would carry — the checkout's
   * only source of money.
   *
   * It replaces the `deliveryApi.quote` call the checkout used to make, rather
   * than sitting beside it: the delivery figures come back in `preview.delivery`
   * from the same server-side pricing the total came from. Two calls would mean
   * two courier quotes for one basket, and two chances to show a fee that was
   * never charged.
   *
   * Public. A guest is priced from the session basket, exactly like the quote
   * it replaces.
   */
  preview: (data: {
    delivery_method: 'delivery' | 'pickup';
    latitude?: number | null;
    longitude?: number | null;
    address?: string | null;
    promo_code?: string | null;
    session_id?: string | null;
    /** Identity, for the coupon only — see `promoApi.validate`. */
    email?: string | null;
    phone?: string | null;
  }) => api.post<OrderPreview>('/orders/preview', data),
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

  /**
   * Whether the signed-in account may be offered in-page Apple Pay.
   *
   * Server-gated to a test allowlist and to Stripe being the active card
   * gateway — a guest or any other account gets `{ eligible: false }`, so the
   * option cannot render for them however the client is coaxed. `amount` is the
   * total being quoted, used only to route the gateway check.
   */
  applePayEligibility: (amount?: number) =>
    api.get<ApplePayEligibility>(
      `/payments/apple-pay/eligibility${amount && amount > 0 ? `?amount=${amount}` : ''}`,
    ),

  /**
   * Mint a Stripe PaymentIntent so the browser can take an Apple Pay payment
   * for an order in-page. The intent settles through the same webhook every
   * card payment already uses; this only returns the secret to confirm against.
   */
  createApplePayIntent: (orderNumber: string) =>
    api.post<ApplePayIntent>('/payments/apple-pay/intent', {
      order_number: orderNumber,
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
   * **Nothing in the storefront calls this any more.** The checkout was its
   * only caller and now uses `ordersApi.preview`, which returns this same
   * answer inside a fully priced order — one call to the courier instead of
   * two, and no chance of the delivery line and the total disagreeing. Kept
   * because `POST /delivery/quote` is still served and still the right thing
   * for a surface that wants the fee without an order behind it.
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
