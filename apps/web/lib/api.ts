import { Cart, Product, ProductListResponse, TokenResponse, User, PromoValidateResponse, Order, Address, AddressCreate, OrderCreate, PaymentSessionResponse } from './types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

// ─── Error ────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ─── Token & Session helpers ──────────────────────────────────────────────────

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('mm_token');
}

export function setToken(token: string): void {
  localStorage.setItem('mm_token', token);
}

export function clearToken(): void {
  localStorage.removeItem('mm_token');
}

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('mm_refresh_token');
}

export function setRefreshToken(token: string): void {
  localStorage.setItem('mm_refresh_token', token);
}

export function clearRefreshToken(): void {
  localStorage.removeItem('mm_refresh_token');
}

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

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  const res = await fetch(`${API_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!res.ok) {
    clearToken();
    clearRefreshToken();
    return null;
  }

  const data: TokenResponse = await res.json();
  setToken(data.access_token);
  setRefreshToken(data.refresh_token);
  return data.access_token;
}

// ─── Core fetch ───────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}, _retry = true): Promise<T> {
  const token = getToken();
  const sessionId = getSessionId();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (sessionId) headers['X-Session-Id'] = sessionId;

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (res.status === 401 && _retry) {
    const newToken = await refreshAccessToken();
    if (newToken) {
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

// ─── HTTP methods ─────────────────────────────────────────────────────────────

export const api = {
  get:    <T>(path: string)                  => request<T>(path),
  post:   <T>(path: string, data?: unknown)  => request<T>(path, { method: 'POST',   body: JSON.stringify(data) }),
  put:    <T>(path: string, data?: unknown)  => request<T>(path, { method: 'PUT',    body: JSON.stringify(data) }),
  patch:  <T>(path: string, data?: unknown)  => request<T>(path, { method: 'PATCH',  body: JSON.stringify(data) }),
  delete: <T>(path: string)                  => request<T>(path, { method: 'DELETE' }),
};

// ─── Typed endpoints ──────────────────────────────────────────────────────────

export const authApi = {
  register: async (data: { email: string; password: string; first_name: string; last_name: string; phone?: string }) => {
    const resp = await api.post<TokenResponse>('/auth/register', data);
    setToken(resp.access_token);
    setRefreshToken(resp.refresh_token);
    return resp;
  },
  login: async (email: string, password: string) => {
    const resp = await api.post<TokenResponse>('/auth/login', { email, password });
    setToken(resp.access_token);
    setRefreshToken(resp.refresh_token);
    return resp;
  },
  guest: async (email?: string) => {
    const resp = await api.post<TokenResponse>('/auth/guest', { email });
    setToken(resp.access_token);
    setRefreshToken(resp.refresh_token);
    return resp;
  },
  refresh: () => api.post<TokenResponse>('/auth/refresh', { refresh_token: getRefreshToken() }),
  logout: async () => {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      await api.post<void>('/auth/logout', { refresh_token: refreshToken }).catch(() => {});
    }
    clearToken();
    clearRefreshToken();
  },
  me: () => api.get<User>('/auth/me'),
  updateMe: (data: { first_name?: string; last_name?: string; phone?: string }) =>
    api.put<User>('/auth/me', data),
  forgotPassword: (email: string) =>
    api.post<{ message: string }>('/auth/forgot-password', { email }),
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
  addItem: (variant_id: string, quantity: number) =>
    api.post<Cart>('/cart/items', { variant_id, quantity }),
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
  get: (orderNumber: string) => api.get<Order>(`/orders/${orderNumber}`),
};

export const paymentsApi = {
  createSession: (orderNumber: string, provider: string) =>
    api.post<PaymentSessionResponse>('/payments/create-session', {
      order_number: orderNumber,
      provider,
    }),
};
