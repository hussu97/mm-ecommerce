import type {
  AnalyticsOverview, Category, CustomerBreakdown, EmirateData,
  FunnelData, ImportResult, Modifier, Order, OrdersPoint, PaginatedCustomers,
  PaginatedOrders, Product, ProductListResponse, PromoCode, PromoPerformance,
  RevenueBreakdown, RevenuePoint, TokenResponse, TopProduct, TrafficData,
  UploadResponse, User,
} from './types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

// ─── Error ────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ─── Token helpers ────────────────────────────────────────────────────────────

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('mm_admin_token');
}

export function setToken(token: string): void {
  localStorage.setItem('mm_admin_token', token);
}

export function clearToken(): void {
  localStorage.removeItem('mm_admin_token');
}

// ─── Core fetch ───────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const isFormData = options.body instanceof FormData;

  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers as Record<string, string>),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

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
  delete: <T>(path: string)                         => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, formData: FormData)     => request<T>(path, { method: 'POST', body: formData }),
};

// ─── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  login: (email: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { email, password }),
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

// ─── Products ─────────────────────────────────────────────────────────────────

export const productsApi = {
  list: (params?: { search?: string; category?: string[]; page?: number; per_page?: number; include_inactive?: boolean; is_active?: boolean }) => {
    if (!params) return api.get<ProductListResponse>('/products');
    const p = new URLSearchParams();
    if (params.search) p.set('search', params.search);
    params.category?.forEach(c => p.append('category', c));
    if (params.page !== undefined) p.set('page', String(params.page));
    if (params.per_page !== undefined) p.set('per_page', String(params.per_page));
    if (params.include_inactive !== undefined) p.set('include_inactive', String(params.include_inactive));
    if (params.is_active !== undefined) p.set('is_active', String(params.is_active));
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
  listAll: (params?: { status?: string; search?: string; page?: number; per_page?: number }) => {
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
  emirates: (params?: { start_date?: string; end_date?: string }) =>
    api.get<EmirateData[]>(`/analytics/emirates${buildQs(params)}`),
  promos: (params?: { start_date?: string; end_date?: string }) =>
    api.get<PromoPerformance[]>(`/analytics/promos${buildQs(params)}`),
};

// ─── Customers ────────────────────────────────────────────────────────────────

export const customersApi = {
  list: (params?: { search?: string; page?: number; per_page?: number }) =>
    api.get<PaginatedCustomers>(`/users/admin/all${buildQs(params)}`),
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
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API_URL}/export/${entity}`, { headers });
    if (!res.ok) throw new ApiError(res.status, `Export failed: HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = EXPORT_FILENAMES[entity] ?? `${entity}.csv`;
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
