import type {
  AdminLoginOptions, AdminPasskey, AdminUserSummary,
  AnalyticsOverview, AuditLog, Category, CmsPage, CustomerBreakdown, ZoneSalesData,
  FunnelData, ImportResult, Language, Modifier, Order, OrdersPoint, PaginatedAuditLogs,
  PaginatedCustomers, PaginatedEmailLogs, PaginatedOrders, Product, ProductListResponse,
  PromoCode, PromoPerformance, RevenueBreakdown, RevenuePoint, TokenResponse, TopProduct,
  TrafficData, UploadResponse, User, DeliverySettings, SalesChannel,
  DeliveryMapVersion, DeliveryPricingMode, DeliveryZone, DeliveryZoneSummary, FulfilmentProvider, OrderDelivery,
  BatchGroup,
  BatchWindow, BatchWindowWrite, DeliveryBatch, DeliveryZoneMap,
  PaginatedWebhookLogs, WebhookLogDetail, WebhookLogFacets,
  PaymentGateway, PaymentGatewayUpdate,
} from './types';
import type {
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

// ─── Error ────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ─── Refresh access token ─────────────────────────────────────────────────────

async function refreshAccessToken(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({}),
  });
  return res.ok;
}

// ─── Core fetch ───────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}, _retry = true): Promise<T> {
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
    throw new ApiError(res.status, body.detail || `HTTP ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const api = {
  get:    <T>(path: string)                         => request<T>(path),
  post:   <T>(path: string, data?: unknown)         => request<T>(path, { method: 'POST', body: JSON.stringify(data) }),
  put:    <T>(path: string, data?: unknown)         => request<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch:  <T>(path: string, data?: unknown)         => request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: <T>(path: string)                         => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, formData: FormData)     => request<T>(path, { method: 'POST', body: formData }),
};

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
  list: (params?: { search?: string; category?: string[]; page?: number; per_page?: number; include_inactive?: boolean; is_active?: boolean; sort?: string; channel?: 'web' | 'pos' | 'all' }) => {
    if (!params) return api.get<ProductListResponse>('/products?channel=all');
    const p = new URLSearchParams();
    p.set('channel', params.channel ?? 'all');
    if (params.search) p.set('search', params.search);
    params.category?.forEach(c => p.append('category', c));
    if (params.page !== undefined) p.set('page', String(params.page));
    if (params.per_page !== undefined) p.set('per_page', String(params.per_page));
    if (params.include_inactive !== undefined) p.set('include_inactive', String(params.include_inactive));
    if (params.is_active !== undefined) p.set('is_active', String(params.is_active));
    if (params.sort) p.set('sort', params.sort);
    const qs = p.toString();
    return api.get<ProductListResponse>(`/products${qs ? '?' + qs : ''}`);
  },
  get: (slug: string) => api.get<Product>(`/products/${slug}`),
  create: (data: object) => api.post<Product>('/products', data),
  update: (slug: string, data: object) => api.put<Product>(`/products/${slug}`, data),
  delete: (slug: string) => api.delete<void>(`/products/${slug}`),
  linkModifier: (slug: string, data: object) => api.post<Product>(`/products/${slug}/modifiers`, data),
  unlinkModifier: (slug: string, modifierId: string) => api.delete<Product>(`/products/${slug}/modifiers/${modifierId}`),
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
    /** `online` for the storefront, `counter` for the till. Omit for both. */
    channel?: string;
    branch_id?: string;
    page?: number;
    per_page?: number;
  }) => {
    const qs = params
      ? '?' + new URLSearchParams(
          Object.entries(params)
            .filter(([, v]) => v !== undefined && v !== '')
            .map(([k, v]) => [k, String(v)])
        ).toString()
      : '';
    return api.get<PaginatedOrders>(`/orders/admin/all${qs}`);
  },
  get: (orderNumber: string) => api.get<Order>(`/orders/${orderNumber}`),
  updateStatus: (orderNumber: string, status: string, admin_notes?: string) =>
    api.put<Order>(`/orders/${orderNumber}/status`, { status, admin_notes }),
  /** Fulfilment detail. 404s for pickup orders and anything placed before this existed. */
  getDelivery: (orderNumber: string) =>
    api.get<OrderDelivery>(`/orders/${orderNumber}/delivery`),
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
  listBatches: (params?: { status_filter?: string; limit?: number }) =>
    api.get<DeliveryBatch[]>(`/delivery-zones/batches${buildQs(params)}`),
  dispatchBatch: (batchId: string) =>
    api.post<DeliveryBatch>(`/delivery-zones/batches/${batchId}/dispatch`),
};

// ─── Analytics ────────────────────────────────────────────────────────────────

type DateParams = { start_date?: string; end_date?: string; group_by?: string };

function buildQs(params?: Record<string, string | number | undefined>): string {
  if (!params) return '';
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== '');
  if (!entries.length) return '';
  return '?' + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString();
}

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

export const exportApi = {
  download: async (entity: string) => {
    const res = await fetch(`${API_BASE}/export/${entity}`, { credentials: 'include' });
    if (!res.ok) throw new ApiError(res.status, `Export failed: HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = EXPORT_FILENAMES[entity] ?? `${entity}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },
  exportOrders: async (params?: { start_date?: string; end_date?: string; status?: string }) => {
    const qs = params
      ? '?' + new URLSearchParams(
          Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])
        ).toString()
      : '';
    const res = await fetch(`${API_BASE}/export/orders${qs}`, { credentials: 'include' });
    if (!res.ok) throw new ApiError(res.status, `Export failed: HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'orders.csv';
    a.click();
    URL.revokeObjectURL(url);
  },
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
