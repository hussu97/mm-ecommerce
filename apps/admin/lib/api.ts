import type {
  AdminLoginOptions, AdminPasskey, AdminUserSummary,
  AnalyticsOverview, AuditLog, Category, CmsPage, CustomerBreakdown, DashboardToday, ZoneSalesData,
  FunnelData, ImportResult, Language, Modifier, Order, OrdersPoint, PaginatedAuditLogs,
  PaginatedCustomers, PaginatedEmailLogs, PaginatedLiveCarts, PaginatedOrders, Product, ProductListResponse,
  PromoCode, Promotion, PromoPerformance, RevenueBreakdown, RevenuePoint, TokenResponse, TopProduct,
  TrafficData, UploadResponse, User, DeliverySettings, SalesChannel,
  DeliveryMapVersion, DeliveryPricingMode, DeliveryZone, DeliveryZoneSummary, FulfilmentProvider, OrderDelivery, OrderEconomics,
  BatchGroup,
  BatchWindow, BatchWindowWrite, Courier, CourierWrite, DeliveryBatch, DeliveryZoneMap,
  PaginatedWebhookLogs, WebhookLogDetail, WebhookLogFacets,
  PaymentGateway, PaymentGatewayUpdate,
  LalamoveQuote, OrderStatusEvent,
  FulfilmentOptions, FulfilmentQuote,
  BranchProductAvailability, StockDuration,
  UrlRedirect,
  GrubOpsLocation,
  GrubOpsOrderList,
} from './types';
import type { Schemas } from '@mm/types';
import type {
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';

// Aggregator reconciliation + branch-map shapes come straight from the generated
// contract (rule 8); these aliases keep the friendly names the code below uses.
type ReconList = Schemas['AggregatorReconciliationList'];
type ReconSummary = Schemas['ReconSummaryOut'];
type SyncRunList = Schemas['AggregatorSyncRunList'];
type RunTriggerResult = Schemas['AggregatorRunTriggerOut'];
type BranchMapRow = Schemas['AggregatorBranchMapOut'];
type BranchMapInput = Schemas['AggregatorBranchMapIn'];
type AggregatorAccount = Schemas['AggregatorAccountPublic'];
type AggregatorAccountInput = Schemas['AggregatorAccountPush'];

// The unified external-system item map (GrubOps + every aggregator) — one table,
// one generic API. Names straight from the generated contract (rule 8).
type ItemMappingList = Schemas['ItemMappingList'];
type ItemMappingResponse = Schemas['ItemMappingResponse'];
type ItemMappingUpdate = Schemas['ItemMappingUpdate'];
type ItemMappingSyncSummary = Schemas['ItemMappingSyncSummary'];

/**
 * Where the API lives, from the browser's point of view.
 *
 * Relative by default, so requests ride the same-origin rewrite in
 * `next.config.ts` (`/api/v1/:path*` → the backend) and the auth cookies stay
 * first-party — the same pattern the storefront uses. The old absolute
 * `http://localhost:8000` fallback went cross-origin in dev while the rewrite
 * sat unused. Production sets `NEXT_PUBLIC_API_URL` explicitly.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/v1';

// ─── Error ────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  /**
   * FastAPI's `detail`, verbatim, when it is not a plain string.
   *
   * Most endpoints raise `HTTPException(400, "some sentence")` and `message` is
   * the whole answer. A few carry structure — an expired Lalamove quotation
   * comes back as `{message, quote}` so the dialog can show the new price
   * instead of asking again blind — and `super(message)` would stringify that
   * to `[object Object]`.
   */
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ─── Refresh access token ─────────────────────────────────────────────────────

/**
 * One refresh at a time, shared by every caller.
 *
 * When a page fires several requests and the access token has just expired,
 * all of them 401 together. Each used to start its own refresh — the POS
 * bindings even kept a second private copy of this function — and two racing
 * refreshes can rotate the cookie out from under each other, logging the
 * admin out for no reason. Concurrent 401s now await the same in-flight call.
 */
let refreshInFlight: Promise<boolean> | null = null;

function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({}),
  })
    .then(res => res.ok)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

// ─── Core fetch ───────────────────────────────────────────────────────────────

/**
 * The one fetch wrapper for the whole console — the ecommerce bindings below
 * and the POS bindings in `pos-api.ts` all go through it. Cookie auth, one
 * 401 refresh-and-retry, and error unwrapping that keeps a structured
 * `detail` intact (an object stringified into a message reads as
 * "[object Object]", which is what the POS side's private copy used to show).
 */
export async function request<T>(path: string, options: RequestInit = {}, _retry = true): Promise<T> {
  const isFormData = options.body instanceof FormData;

  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers as Record<string, string>),
  };

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
    const detail = body.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : (detail?.message as string) || `HTTP ${res.status}`;
    throw new ApiError(res.status, message, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// A missing payload is sent as `{}` rather than as no body at all: some POS
// endpoints declare a body model whose fields are all optional, and those
// accept an empty object where they would reject an absent body.
export const api = {
  get:    <T>(path: string)                         => request<T>(path),
  post:   <T>(path: string, data?: unknown)         => request<T>(path, { method: 'POST', body: JSON.stringify(data ?? {}) }),
  put:    <T>(path: string, data?: unknown)         => request<T>(path, { method: 'PUT', body: JSON.stringify(data ?? {}) }),
  patch:  <T>(path: string, data?: unknown)         => request<T>(path, { method: 'PATCH', body: JSON.stringify(data ?? {}) }),
  delete: <T>(path: string)                         => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, formData: FormData)     => request<T>(path, { method: 'POST', body: formData }),
};

// ─── Query strings ────────────────────────────────────────────────────────────

/**
 * The one query-string builder, shared with `pos-api.ts`.
 *
 * `undefined`, `null` and `''` all mean "don't send the filter" — an empty
 * string in a select is "All", not a value the API should see. `false` IS
 * sent: `is_active=false` is the Inactive tab, not the absence of a filter.
 * Arrays repeat the key (`category=a&category=b`), which is how FastAPI reads
 * a multi-value query param.
 */
export function buildQs(
  params?: Record<string, string | number | boolean | string[] | undefined | null>,
): string {
  if (!params) return '';
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) v.forEach(item => search.append(k, String(item)));
    else search.set(k, String(v));
  }
  const out = search.toString();
  return out ? `?${out}` : '';
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  login: (email: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { email, password }),
  adminLoginOptions: (email: string) =>
    api.post<AdminLoginOptions>('/auth/admin/login-options', { email }),
  passkeyRegistrationOptions: () =>
    api.post<{ options: PublicKeyCredentialCreationOptionsJSON }>('/auth/admin/passkeys/register/options', {}),
  passkeyRegistrationVerify: (credential: unknown, name?: string) =>
    api.post<AdminPasskey>('/auth/admin/passkeys/register/verify', { credential, name }),
  passkeyLoginOptions: (email: string) =>
    api.post<{ options: PublicKeyCredentialRequestOptionsJSON }>('/auth/admin/passkeys/login/options', { email }),
  passkeyLoginVerify: (email: string, credential: unknown) =>
    api.post<TokenResponse>('/auth/admin/passkeys/login/verify', { email, credential }),
  passkeys: () => api.get<AdminPasskey[]>('/auth/admin/passkeys'),
  deletePasskey: (id: string) => api.delete<void>(`/auth/admin/passkeys/${id}`),
  logout: () => api.post<void>('/auth/logout', {}).catch(() => {}),
  me: () => api.get<User>('/auth/me'),
};

// ─── Categories ───────────────────────────────────────────────────────────────

export const categoriesApi = {
  list: (includeInactive = true) =>
    api.get<Category[]>(`/categories?include_inactive=${includeInactive}`),
  create: (data: Partial<Category>) => api.post<Category>('/categories', data),
  update: (slug: string, data: Partial<Category>) => api.put<Category>(`/categories/${slug}`, data),
  delete: (slug: string) => api.delete<void>(`/categories/${slug}`),
};

// ─── Menu groups ──────────────────────────────────────────────────────────────

/** A node of the register's menu tree, with its descendants nested inside. */
export interface MenuGroupNode {
  id: string;
  name: string;
  name_localized?: string | null;
  reference?: string | null;
  image_url?: string | null;
  parent_id?: string | null;
  display_order: number;
  is_active: boolean;
  product_ids: string[];
  product_count: number;
  children: MenuGroupNode[];
}

export interface MenuGroupInput {
  name: string;
  name_localized?: string | null;
  parent_id?: string | null;
  display_order?: number;
  is_active?: boolean;
  product_ids?: string[];
}

export const menuGroupsApi = {
  tree: (includeInactive = true) =>
    api.get<MenuGroupNode[]>(`/menu-groups/tree?include_inactive=${includeInactive}`),
  create: (data: MenuGroupInput) => api.post<MenuGroupNode>('/menu-groups', data),
  update: (id: string, data: Partial<MenuGroupInput>) =>
    api.patch<MenuGroupNode>(`/menu-groups/${id}`, data),
  // Takes every group nested underneath it with it.
  delete: (id: string) => api.delete<void>(`/menu-groups/${id}`),
};

// ─── Products ─────────────────────────────────────────────────────────────────

export const productsApi = {
  /**
   * The console manages every product, so these default to `channel: 'all'`.
   *
   * The API defaults to `web` — deliberately, so a forgotten parameter cannot
   * put counter items on a cake website. That default is wrong here in the
   * opposite direction: without this the console could only see the 39
   * web-visible products and had no way to reach the 92 sold only at the till.
   */
  list: (params?: { search?: string; category?: string[]; page?: number; per_page?: number; include_inactive?: boolean; is_active?: boolean; sort?: string; channel?: 'web' | 'pos' | 'all' }) =>
    api.get<ProductListResponse>(`/products${buildQs({ ...params, channel: params?.channel ?? 'all' })}`),
  get: (slug: string) => api.get<Product>(`/products/${slug}`),
  create: (data: object) => api.post<Product>('/products', data),
  update: (slug: string, data: object) => api.put<Product>(`/products/${slug}`, data),
  delete: (slug: string) => api.delete<void>(`/products/${slug}`),
  linkModifier: (slug: string, data: object) => api.post<Product>(`/products/${slug}/modifiers`, data),
  unlinkModifier: (slug: string, modifierId: string) => api.delete<Product>(`/products/${slug}/modifiers/${modifierId}`),

  /**
   * Every branch override in the estate, in one call.
   *
   * The tables are exception-only, so this is a few dozen rows however large
   * the catalogue gets — which is what lets the product list draw a per-branch
   * column without a request per row.
   */
  branchAvailability: () =>
    api.get<BranchProductAvailability[]>('/products/availability'),

  /**
   * Mark a product in or out of stock at one branch, or set its branch price.
   *
   * `duration` only means anything on the way out. Putting something back
   * clears the countdown with the flag, which the API enforces.
   */
  setBranchAvailability: (
    productId: string,
    data: {
      branch_id: string;
      is_in_stock?: boolean;
      is_active?: boolean;
      price?: number;
      duration?: StockDuration;
    },
  ) =>
    api.put<BranchProductAvailability>(`/products/${productId}/availability`, data),
};

// ─── Modifiers ────────────────────────────────────────────────────────────────

export const modifiersApi = {
  list: (includeInactive = false) => api.get<Modifier[]>(`/modifiers?include_inactive=${includeInactive}`),
  get: (id: string) => api.get<Modifier>(`/modifiers/${id}`),
  create: (data: object) => api.post<Modifier>('/modifiers', data),
  update: (id: string, data: object) => api.put<Modifier>(`/modifiers/${id}`, data),
  delete: (id: string) => api.delete<void>(`/modifiers/${id}`),
  addOption: (modifierId: string, data: object) => api.post<Modifier>(`/modifiers/${modifierId}/options`, data),
  updateOption: (modifierId: string, optionId: string, data: object) => api.put<Modifier>(`/modifiers/${modifierId}/options/${optionId}`, data),
  deleteOption: (modifierId: string, optionId: string) => api.delete<Modifier>(`/modifiers/${modifierId}/options/${optionId}`),
};

// ─── Import ───────────────────────────────────────────────────────────────────

async function uploadCsv(path: string, file: File): Promise<ImportResult> {
  const fd = new FormData();
  fd.append('file', file);
  return api.upload<ImportResult>(path, fd);
}

export const importApi = {
  categories: (file: File) => uploadCsv('/import/categories', file),
  products: (file: File) => uploadCsv('/import/products', file),
  modifiers: (file: File) => uploadCsv('/import/modifiers', file),
  modifierOptions: (file: File) => uploadCsv('/import/modifier-options', file),
  productModifiers: (file: File) => uploadCsv('/import/product-modifiers', file),
};

// ─── Orders ───────────────────────────────────────────────────────────────────

export const ordersApi = {
  listAll: (params?: {
    status?: string;
    search?: string;
    /** `online` storefront, `counter` till, `aggregator` marketplace. Omit for all. */
    channel?: string;
    /** One carrier by code — a marketplace (`talabat`…) or a courier (`lalamove`…). */
    courier?: string;
    branch_id?: string;
    page?: number;
    per_page?: number;
  }) => api.get<PaginatedOrders>(`/orders/admin/all${buildQs(params)}`),
  get: (orderNumber: string) => api.get<Order>(`/orders/${orderNumber}`),
  updateStatus: (orderNumber: string, status: string, admin_notes?: string) =>
    api.put<Order>(`/orders/${orderNumber}/status`, { status, admin_notes }),
  /** Fulfilment detail. 404s for pickup orders and anything placed before this existed. */
  getDelivery: (orderNumber: string) =>
    api.get<OrderDelivery>(`/orders/${orderNumber}/delivery`),
  /** What the shop kept: courier cost, processing fee, net and the margins. */
  getEconomics: (orderNumber: string) =>
    api.get<OrderEconomics>(`/orders/${orderNumber}/economics`),
  /** Book the courier again after a failed or abandoned dispatch. */
  dispatchDelivery: (orderNumber: string) =>
    api.post<OrderDelivery>(`/orders/${orderNumber}/delivery/dispatch`),
  /**
   * Ask the courier where this order actually is.
   *
   * For when a status push never arrived. noon Send does not retry a failed
   * webhook and there is nothing to replay, so without this a rider could
   * deliver an order and the shop would still be looking at "assigned"
   * tomorrow. It pulls the live status, the rider's name and number, and walks
   * the order forward exactly as the push would have.
   */
  refreshDelivery: (orderNumber: string) =>
    api.post<OrderDelivery>(`/orders/${orderNumber}/delivery/refresh`),

  /**
   * What Lalamove would charge to carry this packed third-party order.
   *
   * Reads nothing and books nothing — it exists so a human sees the number
   * before any money moves. Valid five minutes; `assignLalamove` books at this
   * exact quotation or refuses.
   */
  quoteLalamove: (orderNumber: string) =>
    api.post<LalamoveQuote>(`/orders/${orderNumber}/delivery/lalamove/quote`),

  /**
   * Hand the order to Lalamove at the price just quoted.
   *
   * A 409 means the quote lapsed; its `detail.quote` carries the current price
   * to re-confirm against.
   */
  assignLalamove: (orderNumber: string, quotationId: string) =>
    api.post<OrderDelivery>(`/orders/${orderNumber}/delivery/lalamove/assign`, {
      quotation_id: quotationId,
    }),

  /**
   * Which couriers this order may be moved to, and why the others are refused.
   *
   * Every refusal comes back as a sentence rather than a missing option: a
   * greyed-out button that will not say why is how somebody ends up ringing a
   * courier to ask a question the screen already knew the answer to.
   */
  fulfilmentOptions: (orderNumber: string) =>
    api.get<FulfilmentOptions>(`/orders/${orderNumber}/delivery/fulfilment-options`),

  /**
   * What moving this order to one courier would cost us. Spends nothing.
   *
   * A Lalamove quote carries an id and expires in five minutes; a noon Send one
   * is a rate card and carries no id, which is not an error.
   */
  quoteFulfilment: (orderNumber: string, provider: FulfilmentProvider) =>
    api.post<FulfilmentQuote>(`/orders/${orderNumber}/delivery/fulfilment-quote`, {
      provider,
    }),

  /**
   * Move the order to a different courier.
   *
   * A 409 means the Lalamove quote lapsed; its `detail.quote` carries the
   * current price to re-confirm against.
   */
  reassignFulfilment: (
    orderNumber: string,
    provider: FulfilmentProvider,
    quotationId?: string | null,
  ) =>
    api.post<OrderDelivery>(`/orders/${orderNumber}/delivery/reassign`, {
      provider,
      quotation_id: quotationId ?? null,
    }),

  /**
   * Give up on the courier holding this order, without replacing them.
   *
   * `acknowledged` has to be true when the exposure says a fee is likely — the
   * API refuses otherwise, so that "somebody was told" is a fact rather than a
   * thing the dialog claims.
   */
  abandonBooking: (orderNumber: string, acknowledged: boolean) =>
    api.post<OrderDelivery>(`/orders/${orderNumber}/delivery/abandon-booking`, {
      acknowledged_charge: acknowledged,
    }),

  /** Every status this order has been through, and who moved it. */
  statusEvents: (orderNumber: string) =>
    api.get<OrderStatusEvent[]>(`/orders/${orderNumber}/status-events`),
};

// ─── Delivery zones ───────────────────────────────────────────────────────────

export const deliveryZonesApi = {
  listVersions: () => api.get<DeliveryMapVersion[]>('/delivery-zones/versions'),
  summary: () => api.get<DeliveryZoneSummary>('/delivery-zones/summary'),
  /** The three numbers that apply in every zone. */
  getSettings: () => api.get<DeliverySettings>('/delivery-zones/settings'),
  updateSettings: (data: Partial<Pick<DeliverySettings, 'free_delivery_threshold' | 'pickup_fee' | 'default_delivery_fee'>>) =>
    api.put<DeliverySettings>('/delivery-zones/settings', data),
  /** Copy a map into an editable draft. Defaults to copying the live one. */
  createVersion: (data: { name: string; notes?: string; source_version_id?: string }) =>
    api.post<DeliveryMapVersion>('/delivery-zones/versions', data),
  /** Only works on a draft — the live map is read-only by design. */
  updateZone: (
    zoneId: string,
    data: {
      delivery_fee?: number;
      pricing_mode?: DeliveryPricingMode;
      free_delivery_eligible?: boolean;
      fulfilment_provider?: FulfilmentProvider;
      /**
       * Where this zone's orders may be moved when the preferred courier will
       * not carry them. Replaces the list wholesale — the API rejects a courier
       * that is already the zone's own.
       */
      alternate_providers?: FulfilmentProvider[];
      /** Null hands the zone back to the default pickup branch. */
      branch_id?: string | null;
      display_order?: number;
    },
  ) => api.put<DeliveryZone>(`/delivery-zones/polygons/${zoneId}`, data),
  publish: (versionId: string) =>
    api.post<DeliveryMapVersion>(`/delivery-zones/versions/${versionId}/activate`),
  deleteVersion: (versionId: string) =>
    api.delete<void>(`/delivery-zones/versions/${versionId}`),
  geometry: (zoneId: string) =>
    api.get<{ name: string; delivery_fee: number; fulfilment_provider: FulfilmentProvider; geometry: unknown }>(
      `/delivery-zones/polygons/${zoneId}/geometry`,
    ),
  /** Every zone's outline in one call, simplified for drawing. */
  map: (versionId?: string) =>
    api.get<DeliveryZoneMap>(`/delivery-zones/map${versionId ? `?version_id=${versionId}` : ''}`),

  // ── Batching ────────────────────────────────────────────────────────────
  //
  // Addressed by group rather than by zone: a schedule governs a set of zones
  // that ride together, and pointing it at one of them was what let two
  // unrelated schedules merge onto a single booking by accident.
  listBatchGroups: () => api.get<BatchGroup[]>('/delivery-zones/batch-groups'),
  listWindows: (groupId: string) =>
    api.get<BatchWindow[]>(`/delivery-zones/batch-groups/${groupId}/batch-windows`),
  createWindow: (groupId: string, data: BatchWindowWrite) =>
    api.post<BatchWindow>(`/delivery-zones/batch-groups/${groupId}/batch-windows`, data),
  updateWindow: (windowId: string, data: BatchWindowWrite) =>
    api.put<BatchWindow>(`/delivery-zones/batch-windows/${windowId}`, data),
  deleteWindow: (windowId: string) =>
    api.delete<void>(`/delivery-zones/batch-windows/${windowId}`),
  /** Minutes-to-door and whether the schedule runs. Immediate, not versioned. */
  updateBatchGroup: (
    groupId: string,
    data: { delivery_minutes_after_dispatch?: number; is_active?: boolean },
  ) => api.put<BatchGroup>(`/delivery-zones/batch-groups/${groupId}`, data),
  listBatches: (params?: { status_filter?: string; limit?: number }) =>
    api.get<DeliveryBatch[]>(`/delivery-zones/batches${buildQs(params)}`),
  dispatchBatch: (batchId: string) =>
    api.post<DeliveryBatch>(`/delivery-zones/batches/${batchId}/dispatch`),

  // ── Courier promises ────────────────────────────────────────────────────
  //
  // What a zone with no batch group is quoted — every noon Send zone and every
  // third-party one. Addressed by courier code, which is the same key the
  // polygons and the groups already hold.
  listCouriers: () => api.get<Courier[]>('/delivery-zones/couriers'),
  updateCourier: (code: string, data: CourierWrite) =>
    api.put<Courier>(`/delivery-zones/couriers/${code}`, data),
};

// ─── Analytics ────────────────────────────────────────────────────────────────

type DateParams = { start_date?: string; end_date?: string; group_by?: string };

// ─── Dashboard (home) ─────────────────────────────────────────────────────────

export const dashboardApi = {
  /**
   * The current trading day at a glance — every order across every channel,
   * aggregated and rounded server-side. No params: it always means "today,
   * where the shop is".
   */
  today: () => api.get<DashboardToday>(`/dashboard/today`),
};

export const analyticsApi = {
  overview: (params?: { start_date?: string; end_date?: string }) =>
    api.get<AnalyticsOverview>(`/analytics/overview${buildQs(params)}`),
  revenue: (params?: DateParams) =>
    api.get<RevenuePoint[]>(`/analytics/revenue${buildQs(params)}`),
  ordersChart: (params?: DateParams) =>
    api.get<OrdersPoint[]>(`/analytics/orders-chart${buildQs(params)}`),
  topProducts: (params?: { start_date?: string; end_date?: string; limit?: number }) =>
    api.get<TopProduct[]>(`/analytics/top-products${buildQs(params)}`),
  funnel: (params?: { start_date?: string; end_date?: string }) =>
    api.get<FunnelData>(`/analytics/funnel${buildQs(params)}`),
  traffic: (params?: { start_date?: string; end_date?: string }) =>
    api.get<TrafficData>(`/analytics/traffic${buildQs(params)}`),
  customers: (params?: { start_date?: string; end_date?: string }) =>
    api.get<CustomerBreakdown>(`/analytics/customers${buildQs(params)}`),
  revenueBreakdown: (params?: { start_date?: string; end_date?: string }) =>
    api.get<RevenueBreakdown>(`/analytics/revenue-breakdown${buildQs(params)}`),
  zones: (params?: { start_date?: string; end_date?: string }) =>
    api.get<ZoneSalesData[]>(`/analytics/zones${buildQs(params)}`),
  promos: (params?: { start_date?: string; end_date?: string }) =>
    api.get<PromoPerformance[]>(`/analytics/promos${buildQs(params)}`),
  /**
   * The baskets people are holding right now.
   *
   * No date range: everything else on the analytics screen answers a question
   * about a period, and this one answers "what is in people's hands", which a
   * start and end date can only get wrong.
   */
  liveCarts: (params?: {
    page?: number;
    per_page?: number;
    search?: string;
    has_email?: boolean;
    min_value?: number;
    idle_minutes_min?: number;
    idle_days_max?: number;
  }) => api.get<PaginatedLiveCarts>(`/analytics/live-carts${buildQs(params)}`),
};

// ─── Customers ────────────────────────────────────────────────────────────────

export const customersApi = {
  list: (params?: { search?: string; page?: number; per_page?: number }) =>
    api.get<PaginatedCustomers>(`/users/admin/all${buildQs(params)}`),
};

export const adminUsersApi = {
  list: () => api.get<AdminUserSummary[]>('/users/admin/admin-users'),
};

// ─── Promo Codes ──────────────────────────────────────────────────────────────

export const promoApi = {
  list: (includeInactive = false) => api.get<PromoCode[]>(`/promo-codes?include_inactive=${includeInactive}`),
  create: (data: object) => api.post<PromoCode>('/promo-codes', data),
  update: (code: string, data: object) => api.put<PromoCode>(`/promo-codes/${code}`, data),
  delete: (code: string) => api.delete<void>(`/promo-codes/${code}`),
};

// ─── Promotions (auto-applied offers, e.g. the standing counter discount) ──────

export const promotionsApi = {
  list: () => api.get<Promotion[]>('/promotions'),
  update: (id: string, data: object) => api.put<Promotion>(`/promotions/${id}`, data),
};

// ─── URL Redirects ────────────────────────────────────────────────────────────

export const redirectApi = {
  list: () => api.get<UrlRedirect[]>('/redirects'),
  create: (data: object) => api.post<UrlRedirect>('/redirects', data),
  update: (id: string, data: object) => api.put<UrlRedirect>(`/redirects/${id}`, data),
  delete: (id: string) => api.delete<void>(`/redirects/${id}`),
};

// ─── GrubOps (aggregator out-of-stock sync) ───────────────────────────────────

export const grubopsApi = {
  /** Every branch GrubOps knows, and whether its stock is being mirrored. */
  locations: () => api.get<GrubOpsLocation[]>('/grubops/locations'),
  /** The per-branch switch. Off for a branch whose register is not live yet. */
  updateLocation: (id: string, data: { is_active?: boolean; grubops_location_id?: string }) =>
    api.put<GrubOpsLocation>(`/grubops/locations/${id}`, data),
  /** The ingest log: aggregator orders that came in, and anything that failed. */
  orders: (params: {
    channel?: string;
    errors_only?: boolean;
    unmapped_only?: boolean;
    /** Match on the external id, channel, GrubOps id or status. */
    search?: string;
    /** `recent` (newest first) or `channel` (alphabetical). */
    sort?: 'recent' | 'channel';
    page?: number;
    page_size?: number;
  }) => api.get<GrubOpsOrderList>(`/grubops/orders${buildQs(params)}`),
};

// ─── Item mappings (unified external-system item map) ─────────────────────────

/**
 * The one review queue for every external-system item map — GrubOps and each
 * aggregator, one table behind one generic API. Replaces the old
 * GrubOps-specific `/grubops/mappings` endpoints.
 *
 * `sync` is a GrubOps-only re-read of their menu (`system=grubops`); it only
 * ever adds suggestions and never overwrites an approved or hand-corrected row,
 * only refreshes its display name.
 */
export const itemMappingsApi = {
  list: (params: {
    /** One external system (`grubops`, `keeta`, `deliveroo`, `talabat`, `noon`, `careem`) or omit for all. */
    system?: string;
    approved?: boolean;
    kind?: string;
    /** Match on our name, the external name, or an external id. */
    search?: string;
    /** `queue` (needs-decision first) or `name` (alphabetical by our name). */
    sort?: 'queue' | 'name';
    page?: number;
    page_size?: number;
  }) => api.get<ItemMappingList>(`/item-mappings${buildQs(params)}`),
  update: (id: string, data: ItemMappingUpdate) =>
    api.put<ItemMappingResponse>(`/item-mappings/${id}`, data),
  sync: (system: string) =>
    api.post<ItemMappingSyncSummary>(`/item-mappings/sync${buildQs({ system })}`, {}),
};

// ─── Aggregator reconciliation ────────────────────────────────────────────────

/**
 * The aggregator↔MM order match: what each marketplace says an order was worth
 * against what our books recorded, and where the two disagree.
 *
 * Paginated by `limit`/`offset` rather than `page`/`per_page` — the endpoint
 * answers with `{ items, total }` and the page turns that into pages itself.
 */
export const reconciliationApi = {
  list: (params: {
    channel?: string;
    branch_id?: string;
    match_status?: string;
    /** Only rows the pass flagged — an item, refund, commission or amount discrepancy. */
    flagged?: boolean;
    limit?: number;
    offset?: number;
  }) => api.get<ReconList>(`/aggregators/reconciliation${buildQs(params)}`),
  /** Per-channel roll-up and a grand total, for the stat cards. */
  summary: (params?: { channel?: string; branch_id?: string }) =>
    api.get<ReconSummary>(`/aggregators/reconciliation/summary${buildQs(params)}`),
};

// ─── Aggregator ingest runs (the Runs table) ──────────────────────────────────
export const aggregatorRunsApi = {
  /** The ingest run trail, newest first, with the total for the filter. */
  list: (params: {
    channel?: string;
    mode?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }) => api.get<SyncRunList>(`/aggregators/runs${buildQs(params)}`),
  /** Kick off a full aggregator pass now (the "Run now" button). Returns as soon
   *  as it starts; the run rows land in the list as each channel finishes. */
  trigger: () => api.post<RunTriggerResult>('/aggregators/runs/trigger'),
};

// ─── Aggregator outlet↔branch mappings ────────────────────────────────────────

/**
 * The outlet↔branch map every aggregator integration reads: which of our
 * branches a marketplace's outlet/brand/company ids point at.
 *
 * `upsert` writes on the pair (`channel`, `branch_id`) — POSTing the same pair
 * again edits that row rather than adding a second, so the console never has to
 * decide between create and update itself. `remove` deletes by row id.
 */
export const branchMapApi = {
  list: (params?: { channel?: string }) =>
    api.get<BranchMapRow[]>(`/aggregators/branch-map${buildQs(params)}`),
  upsert: (data: BranchMapInput) => api.post<BranchMapRow>('/aggregators/branch-map', data),
  remove: (id: string) => api.delete<void>(`/aggregators/branch-map/${id}`),
};

// ─── Aggregator login recipes ─────────────────────────────────────────────────

/**
 * How we sign in to each marketplace: login method (OTP vs not), portal
 * email/password, and the OTP mailbox (per-channel Microsoft Graph app, or
 * IMAP). Secrets are write-only — the GET never returns them. Omit
 * `password` / `mailbox.password` / `mailbox.client_secret` on a later save
 * to keep the stored secret.
 */
export const aggregatorAccountsApi = {
  list: () => api.get<AggregatorAccount[]>('/aggregators/accounts'),
  upsert: (data: AggregatorAccountInput) =>
    api.post<AggregatorAccount>('/aggregators/accounts', data),
};

// ─── Bulk Actions ─────────────────────────────────────────────────────────────

export const bulkApi = {
  updateStatus: (entity: string, ids: string[], is_active: boolean) =>
    api.post<{ updated: number }>(`/bulk/${entity}/status`, { ids, is_active }),
  /**
   * Add or remove one sales channel across a selection.
   *
   * One channel per call: a request carrying the whole list would overwrite
   * the other channel for every product selected, so putting the coffee menu
   * on the register would silently decide whether it belongs on the website.
   */
  updateVisibility: (ids: string[], channel: SalesChannel, enabled: boolean) =>
    api.post<{ updated: number }>('/bulk/products/visibility', { ids, channel, enabled }),
};

// ─── Export ───────────────────────────────────────────────────────────────────

const EXPORT_FILENAMES: Record<string, string> = {
  categories: 'categories.csv',
  products: 'products.csv',
  modifiers: 'modifiers.csv',
  'modifier-options': 'modifier_options.csv',
  'product-modifiers': 'product_modifiers.csv',
};

/**
 * Fetch a file and hand it to the browser as a download.
 *
 * Goes through the 401 refresh-and-retry that `request()` owns, which the two
 * copies of this block did not: an export started on an expired session threw
 * "Export failed: HTTP 401" instead of refreshing and succeeding, and the
 * admin's only clue was that pressing the button again worked.
 *
 * `request()` itself cannot be used, because it parses the body as JSON and
 * this one is a CSV. So the refresh is shared rather than the whole function.
 */
async function downloadBlob(path: string, filename: string): Promise<void> {
  let res = await fetch(`${API_BASE}${path}`, { credentials: 'include' });

  if (res.status === 401 && (await refreshAccessToken())) {
    res = await fetch(`${API_BASE}${path}`, { credentials: 'include' });
  }
  if (!res.ok) throw new ApiError(res.status, `Export failed: HTTP ${res.status}`);

  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export const exportApi = {
  download: (entity: string) =>
    downloadBlob(`/export/${entity}`, EXPORT_FILENAMES[entity] ?? `${entity}.csv`),
  exportOrders: (params?: { start_date?: string; end_date?: string; status?: string }) =>
    downloadBlob(`/export/orders${buildQs(params)}`, 'orders.csv'),
};

// ─── Uploads ──────────────────────────────────────────────────────────────────

export const uploadsApi = {
  uploadImage: (file: File, folder = 'products') => {
    const fd = new FormData();
    fd.append('file', file);
    return api.upload<UploadResponse>(`/uploads/image?folder=${folder}`, fd);
  },
  deleteImage: (key: string) => api.delete<void>(`/uploads/image?key=${encodeURIComponent(key)}`),
};

// ─── Languages ─────────────────────────────────────────────────────────────

export const languagesApi = {
  list: () => api.get<Language[]>('/i18n/languages'),
  listAll: () => api.get<Language[]>('/i18n/languages/all'),
  create: (data: Partial<Language>) => api.post<Language>('/i18n/languages', data),
  update: (code: string, data: Partial<Language>) => api.put<Language>(`/i18n/languages/${code}`, data),
  delete: (code: string) => api.delete<void>(`/i18n/languages/${code}`),
};

// ─── CMS ──────────────────────────────────────────────────────────────────────

export const cmsApi = {
  list: () => api.get<CmsPage[]>('/cms/pages'),
  get: (slug: string) => api.get<CmsPage>(`/cms/pages/${slug}`),
  update: (slug: string, content: Record<string, unknown>) =>
    api.put<CmsPage>(`/cms/pages/${slug}`, { content }),
  updateLocale: (slug: string, locale: string, content: Record<string, unknown>) =>
    api.put<CmsPage>(`/cms/pages/${slug}/${locale}`, { content }),
};

// ─── Email Logs ───────────────────────────────────────────────────────────────

export const emailLogsApi = {
  list: (params?: {
    status?: string;
    template?: string;
    recipient?: string;
    order_number?: string;
    date_from?: string;
    date_to?: string;
    page?: number;
    per_page?: number;
  }) => api.get<PaginatedEmailLogs>(`/email-logs/admin/all${buildQs(params)}`),
};

// ─── Webhook Logs ─────────────────────────────────────────────────────────────

export const webhookLogsApi = {
  list: (params?: {
    provider?: string;
    endpoint?: string;
    event_type?: string;
    order_number?: string;
    external_id?: string;
    // Strings rather than booleans: `buildQs` serialises string | number, and
    // FastAPI parses 'true'/'false' into a bool at the other end.
    matched?: 'true' | 'false';
    errors_only?: 'true';
    date_from?: string;
    date_to?: string;
    page?: number;
    per_page?: number;
  }) => api.get<PaginatedWebhookLogs>(`/webhook-logs${buildQs(params)}`),
  /** The bodies, which the list deliberately does not carry. */
  get: (id: string) => api.get<WebhookLogDetail>(`/webhook-logs/${id}`),
  /** The values actually present, so the filters offer real options. */
  facets: () => api.get<WebhookLogFacets>('/webhook-logs/providers'),
};

// ─── Payment Gateways ─────────────────────────────────────────────────────────

/**
 * The switch that decides which processor takes a card.
 *
 * Changes take effect on the very next checkout — the router re-reads these
 * rows on every request rather than caching them, because the whole point is
 * that an incident is answered now and not on the next deploy.
 */
export const paymentGatewaysApi = {
  list: () => api.get<PaymentGateway[]>('/payment-gateways'),
  update: (code: string, data: PaymentGatewayUpdate) =>
    api.patch<PaymentGateway>(`/payment-gateways/${code}`, data),
};

// ─── Audit Logs ───────────────────────────────────────────────────────────────

export const auditLogsApi = {
  list: (params?: {
    action?: string;
    entity_type?: string;
    admin_id?: string;
    search?: string;
    date_from?: string;
    date_to?: string;
    page?: number;
    per_page?: number;
  }) => api.get<PaginatedAuditLogs>(`/audit-logs${buildQs(params)}`),
  get: (id: string) => api.get<AuditLog>(`/audit-logs/${id}`),
};

// ─── Translations ──────────────────────────────────────────────────────────

export const translationsApi = {
  get: (locale: string) => api.get<Record<string, string>>(`/i18n/translations/${locale}`),
  bulkUpsert: (locale: string, namespace: string, translations: Array<{ key: string; value: string }>) =>
    api.put<{ updated: number }>(`/i18n/translations/${locale}`, { namespace, translations }),
};

// ─── Custom cake orders ────────────────────────────────────────────────────

export interface CustomOrder {
  id: string;
  due_date: string;
  status: string;
  source: string;
  order_id: string | null;
  customer_name: string;
  customer_phone: string | null;
  customer_email: string | null;
  description: string;
  cake_message: string | null;
  flavour: string | null;
  size_label: string | null;
  servings: number | null;
  reference_image_urls: string[];
  quoted_total: number | null;
  deposit_amount: number;
  branch_id: string | null;
  product_id: string | null;
  brief: Record<string, unknown>;
  admin_notes: string | null;
}

export interface CalendarDay {
  date: string;
  capacity: number;
  booked: number;
  remaining: number;
  is_blackout: boolean;
  blackout_reason: string | null;
  orders: CustomOrder[];
}

export interface CustomOrderBlackout {
  id: string;
  blackout_date: string;
  reason: string | null;
}

export const customOrdersApi = {
  /** The month view: every date in the window with what is booked on it. */
  calendar: (start: string, end: string) =>
    api.get<CalendarDay[]>(`/admin/custom-orders/calendar${buildQs({ start, end })}`),
  list: (params?: { status?: string; upcoming_only?: string }) =>
    api.get<CustomOrder[]>(`/admin/custom-orders${buildQs(params)}`),
  create: (data: object) => api.post<CustomOrder>('/admin/custom-orders', data),
  update: (id: string, data: object) =>
    api.put<CustomOrder>(`/admin/custom-orders/${id}`, data),
  setStatus: (id: string, status: string) =>
    api.put<CustomOrder>(`/admin/custom-orders/${id}/status`, { status }),
  blackouts: () => api.get<CustomOrderBlackout[]>('/admin/custom-orders/blackouts'),
  addBlackout: (blackout_date: string, reason: string | null) =>
    api.post<CustomOrderBlackout>('/admin/custom-orders/blackouts', {
      blackout_date,
      reason,
    }),
  removeBlackout: (id: string) =>
    api.delete<void>(`/admin/custom-orders/blackouts/${id}`),
};
