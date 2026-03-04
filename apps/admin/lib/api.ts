import type {
  Category, Order, PaginatedOrders, Product, ProductListResponse,
  ProductVariant, PromoCode, TokenResponse, UploadResponse, User,
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
  list: (params?: { search?: string; category?: string; page?: number; per_page?: number }) => {
    const qs = params
      ? '?' + new URLSearchParams(
          Object.entries(params)
            .filter(([, v]) => v !== undefined && v !== '')
            .map(([k, v]) => [k, String(v)])
        ).toString()
      : '';
    return api.get<ProductListResponse>(`/products${qs}`);
  },
  get: (slug: string) => api.get<Product>(`/products/${slug}`),
  create: (data: object) => api.post<Product>('/products', data),
  update: (slug: string, data: object) => api.put<Product>(`/products/${slug}`, data),
  delete: (slug: string) => api.delete<void>(`/products/${slug}`),
  addVariant: (slug: string, data: object) => api.post<ProductVariant>(`/products/${slug}/variants`, data),
  updateVariant: (variantId: string, data: object) => api.put<ProductVariant>(`/products/variants/${variantId}`, data),
  deleteVariant: (variantId: string) => api.delete<void>(`/products/variants/${variantId}`),
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
  updateStatus: (orderNumber: string, status: string, admin_notes?: string) =>
    api.put<Order>(`/orders/${orderNumber}/status`, { status, admin_notes }),
};

// ─── Promo Codes ──────────────────────────────────────────────────────────────

export const promoApi = {
  list: () => api.get<PromoCode[]>('/promo-codes'),
  create: (data: object) => api.post<PromoCode>('/promo-codes', data),
  update: (code: string, data: object) => api.put<PromoCode>(`/promo-codes/${code}`, data),
  delete: (code: string) => api.delete<void>(`/promo-codes/${code}`),
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
