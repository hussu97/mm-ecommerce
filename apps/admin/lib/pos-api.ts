// API bindings for the POS domain. Reuses the shared fetch wrapper's behaviour
// (cookie auth, 401 refresh-and-retry) by going through the same request path.

import { API_BASE, ApiError } from './api';
import type {
  Branch, BusinessSettings, Charge, CostOfGoods, Device, DrawerOperation,
  InventoryCategory, InventoryItem, InventoryLevel, InventoryTransaction,
  InventoryValuation, KitchenFlow, MenuEngineeringRow, PaymentMethod,
  PaymentReportRow, PermissionCatalogue, PosOrder, Printer, PurchaseOrder,
  Reason, Role, SalesBreakdownRow, SalesSummary, Staff, Supplier, Tag, Tax,
  TaxGroup, TaxReportRow, Till, Warehouse,
} from './pos-types';

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

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers as Record<string, string>) },
    credentials: 'include',
  });

  if (res.status === 401 && retry) {
    if (await refreshAccessToken()) return request<T>(path, options, false);
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
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, d?: unknown) => request<T>(p, { method: 'POST', body: JSON.stringify(d ?? {}) }),
  put: <T>(p: string, d?: unknown) => request<T>(p, { method: 'PUT', body: JSON.stringify(d ?? {}) }),
  del: <T>(p: string) => request<T>(p, { method: 'DELETE' }),
};

function qs(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return '';
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') search.set(k, String(v));
  });
  const out = search.toString();
  return out ? `?${out}` : '';
}

// ─── Branches & floor plan ────────────────────────────────────────────────────

export const branchesApi = {
  list: (includeDeleted = false) => api.get<Branch[]>(`/branches${qs({ include_deleted: includeDeleted })}`),
  get: (id: string) => api.get<Branch>(`/branches/${id}`),
  create: (data: Partial<Branch>) => api.post<Branch>('/branches', data),
  update: (id: string, data: Partial<Branch>) => api.put<Branch>(`/branches/${id}`, data),
  remove: (id: string) => api.del<void>(`/branches/${id}`),
  businessDays: (id: string, limit = 30) => api.get<unknown[]>(`/branches/${id}/business-days${qs({ limit })}`),
  closeBusinessDay: (id: string) => api.post<unknown>(`/branches/${id}/business-days/close`),
  sections: (id: string) => api.get<unknown[]>(`/branches/${id}/sections`),
};

// ─── Configuration ────────────────────────────────────────────────────────────

export const taxesApi = {
  list: () => api.get<Tax[]>('/taxes'),
  create: (d: Partial<Tax>) => api.post<Tax>('/taxes', d),
  update: (id: string, d: Partial<Tax>) => api.put<Tax>(`/taxes/${id}`, d),
  remove: (id: string) => api.del<void>(`/taxes/${id}`),
};

export const taxGroupsApi = {
  list: () => api.get<TaxGroup[]>('/tax-groups'),
  create: (d: Record<string, unknown>) => api.post<TaxGroup>('/tax-groups', d),
  update: (id: string, d: Record<string, unknown>) => api.put<TaxGroup>(`/tax-groups/${id}`, d),
  remove: (id: string) => api.del<void>(`/tax-groups/${id}`),
};

export const paymentMethodsApi = {
  list: () => api.get<PaymentMethod[]>('/payment-methods'),
  create: (d: Partial<PaymentMethod>) => api.post<PaymentMethod>('/payment-methods', d),
  update: (id: string, d: Partial<PaymentMethod>) => api.put<PaymentMethod>(`/payment-methods/${id}`, d),
  remove: (id: string) => api.del<void>(`/payment-methods/${id}`),
};

export const chargesApi = {
  list: () => api.get<Charge[]>('/charges'),
  create: (d: Partial<Charge>) => api.post<Charge>('/charges', d),
  update: (id: string, d: Partial<Charge>) => api.put<Charge>(`/charges/${id}`, d),
  remove: (id: string) => api.del<void>(`/charges/${id}`),
};

export const reasonsApi = {
  list: (type?: string) => api.get<Reason[]>(`/reasons${qs({ type })}`),
  create: (d: Partial<Reason>) => api.post<Reason>('/reasons', d),
  update: (id: string, d: Partial<Reason>) => api.put<Reason>(`/reasons/${id}`, d),
  remove: (id: string) => api.del<void>(`/reasons/${id}`),
};

export const tagsApi = {
  list: (type?: string) => api.get<Tag[]>(`/tags${qs({ type })}`),
  create: (d: Partial<Tag>) => api.post<Tag>('/tags', d),
  update: (id: string, d: Partial<Tag>) => api.put<Tag>(`/tags/${id}`, d),
  remove: (id: string) => api.del<void>(`/tags/${id}`),
};

export const kitchenFlowsApi = {
  list: (branchId?: string) => api.get<KitchenFlow[]>(`/kitchen-flows${qs({ branch_id: branchId })}`),
  create: (d: Record<string, unknown>) => api.post<KitchenFlow>('/kitchen-flows', d),
  update: (id: string, d: Record<string, unknown>) => api.put<KitchenFlow>(`/kitchen-flows/${id}`, d),
  remove: (id: string) => api.del<void>(`/kitchen-flows/${id}`),
};

export const businessSettingsApi = {
  get: () => api.get<BusinessSettings>('/business-settings'),
  update: (d: Partial<BusinessSettings>) => api.put<BusinessSettings>('/business-settings', d),
};

// ─── Staff & roles ────────────────────────────────────────────────────────────

export const rolesApi = {
  list: () => api.get<Role[]>('/roles'),
  create: (d: Record<string, unknown>) => api.post<Role>('/roles', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Role>(`/roles/${id}`, d),
  remove: (id: string) => api.del<void>(`/roles/${id}`),
  permissions: () => api.get<PermissionCatalogue>('/staff/permissions'),
};

export const staffApi = {
  list: (branchId?: string) => api.get<Staff[]>(`/staff${qs({ branch_id: branchId })}`),
  get: (id: string) => api.get<Staff>(`/staff/${id}`),
  create: (d: Record<string, unknown>) => api.post<Staff>('/staff', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Staff>(`/staff/${id}`, d),
  deactivate: (id: string) => api.del<void>(`/staff/${id}`),
};

// ─── Devices & printers ───────────────────────────────────────────────────────

export const devicesApi = {
  list: (branchId?: string) => api.get<Device[]>(`/devices${qs({ branch_id: branchId })}`),
  create: (d: Record<string, unknown>) => api.post<Device>('/devices', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Device>(`/devices/${id}`, d),
  remove: (id: string) => api.del<void>(`/devices/${id}`),
  issuePairingCode: (id: string) => api.post<Device>(`/devices/${id}/pairing-code`),
  unpair: (id: string) => api.post<Device>(`/devices/${id}/unpair`),
};

export const printersApi = {
  list: (params?: { branch_id?: string; device_id?: string; role?: string }) =>
    api.get<Printer[]>(`/printers${qs(params)}`),
  create: (d: Record<string, unknown>) => api.post<Printer>('/printers', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Printer>(`/printers/${id}`, d),
  remove: (id: string) => api.del<void>(`/printers/${id}`),
};

// ─── Tills ────────────────────────────────────────────────────────────────────

export const tillsApi = {
  list: (params?: { branch_id?: string; business_date?: string; status?: string }) =>
    api.get<Till[]>(`/tills${qs(params)}`),
  get: (id: string) => api.get<Till>(`/tills/${id}`),
  report: (id: string) => api.get<Record<string, unknown>>(`/tills/${id}/report`),
  drawerOperations: (id: string) => api.get<DrawerOperation[]>(`/tills/${id}/drawer-operations`),
};

// ─── POS orders ───────────────────────────────────────────────────────────────

export const posOrdersApi = {
  list: (params?: {
    branch_id?: string; business_date?: string; pos_status?: string;
    order_type?: string; open_only?: boolean; limit?: number;
  }) => api.get<PosOrder[]>(`/pos/orders${qs(params)}`),
  get: (id: string) => api.get<PosOrder>(`/pos/orders/${id}`),
  openChecks: (branchId: string) => api.get<PosOrder[]>(`/pos/kitchen/open-checks${qs({ branch_id: branchId })}`),
  kitchenTickets: (branchId: string, includeCompleted = false) =>
    api.get<unknown[]>(`/pos/kitchen/tickets${qs({ branch_id: branchId, include_completed: includeCompleted })}`),
};

// ─── Inventory ────────────────────────────────────────────────────────────────

export const inventoryApi = {
  categories: () => api.get<InventoryCategory[]>('/inventory/categories'),
  createCategory: (d: Record<string, unknown>) => api.post<InventoryCategory>('/inventory/categories', d),
  updateCategory: (id: string, d: Record<string, unknown>) => api.put<InventoryCategory>(`/inventory/categories/${id}`, d),
  removeCategory: (id: string) => api.del<void>(`/inventory/categories/${id}`),

  items: (params?: { search?: string; category_id?: string }) =>
    api.get<InventoryItem[]>(`/inventory/items${qs(params)}`),
  createItem: (d: Record<string, unknown>) => api.post<InventoryItem>('/inventory/items', d),
  updateItem: (id: string, d: Record<string, unknown>) => api.put<InventoryItem>(`/inventory/items/${id}`, d),
  removeItem: (id: string) => api.del<void>(`/inventory/items/${id}`),

  levels: (params?: { branch_id?: string; warehouse_id?: string; below_minimum_only?: boolean }) =>
    api.get<InventoryLevel[]>(`/inventory/levels${qs(params)}`),

  warehouses: (branchId?: string) => api.get<Warehouse[]>(`/inventory/warehouses${qs({ branch_id: branchId })}`),
  createWarehouse: (d: Record<string, unknown>) => api.post<Warehouse>('/inventory/warehouses', d),

  suppliers: () => api.get<Supplier[]>('/inventory/suppliers'),
  createSupplier: (d: Record<string, unknown>) => api.post<Supplier>('/inventory/suppliers', d),
  updateSupplier: (id: string, d: Record<string, unknown>) => api.put<Supplier>(`/inventory/suppliers/${id}`, d),
  removeSupplier: (id: string) => api.del<void>(`/inventory/suppliers/${id}`),

  transactions: (params?: { branch_id?: string; type?: string; status?: string; business_date?: string }) =>
    api.get<InventoryTransaction[]>(`/inventory/transactions${qs(params)}`),
  createTransaction: (d: Record<string, unknown>, post = true) =>
    api.post<InventoryTransaction>(`/inventory/transactions${qs({ post })}`, d),
  adjust: (d: Record<string, unknown>) => api.post<InventoryTransaction>('/inventory/transactions/adjust', d),

  purchaseOrders: (params?: { branch_id?: string; supplier_id?: string; status?: string }) =>
    api.get<PurchaseOrder[]>(`/inventory/purchase-orders${qs(params)}`),
  createPurchaseOrder: (d: Record<string, unknown>) => api.post<PurchaseOrder>('/inventory/purchase-orders', d),
  submitPurchaseOrder: (id: string) => api.post<PurchaseOrder>(`/inventory/purchase-orders/${id}/submit`),
  approvePurchaseOrder: (id: string) => api.post<PurchaseOrder>(`/inventory/purchase-orders/${id}/approve`),
  declinePurchaseOrder: (id: string) => api.post<PurchaseOrder>(`/inventory/purchase-orders/${id}/decline`),
  receivePurchaseOrder: (id: string, lines: Array<{ purchase_order_item_id: string; quantity: number }>) =>
    api.post<InventoryTransaction>(`/inventory/purchase-orders/${id}/receive`, { lines }),

  productRecipe: (productId: string) => api.get<Record<string, unknown>>(`/inventory/recipes/products/${productId}`),
  setProductRecipe: (productId: string, ingredients: unknown[]) =>
    api.put<Record<string, unknown>>(`/inventory/recipes/products/${productId}`, { ingredients }),
};

// ─── Reports ──────────────────────────────────────────────────────────────────

type Window = { branch_id?: string; date_from?: string; date_to?: string };

export const posReportsApi = {
  salesSummary: (w: Window) => api.get<SalesSummary>(`/pos/reports/sales/summary${qs(w)}`),
  salesBy: (dimension: string, w: Window, limit = 100) =>
    api.get<SalesBreakdownRow[]>(`/pos/reports/sales/by${qs({ dimension, limit, ...w })}`),
  payments: (w: Window) => api.get<PaymentReportRow[]>(`/pos/reports/payments${qs(w)}`),
  taxes: (w: Window) => api.get<TaxReportRow[]>(`/pos/reports/taxes${qs(w)}`),
  voidsReturns: (w: Window) => api.get<Record<string, unknown>[]>(`/pos/reports/voids-returns${qs(w)}`),
  tills: (w: Window) => api.get<Record<string, unknown>[]>(`/pos/reports/tills${qs(w)}`),
  drawerOperations: (w: Window) => api.get<Record<string, unknown>[]>(`/pos/reports/drawer-operations${qs(w)}`),
  inventoryValuation: (branchId?: string) =>
    api.get<InventoryValuation>(`/pos/reports/inventory/valuation${qs({ branch_id: branchId })}`),
  costOfGoods: (w: Window) => api.get<CostOfGoods>(`/pos/reports/inventory/cost-of-goods${qs(w)}`),
  menuEngineering: (w: Window) => api.get<MenuEngineeringRow[]>(`/pos/reports/menu-engineering${qs(w)}`),
};
