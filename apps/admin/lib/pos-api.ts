// API bindings for the POS domain — endpoint definitions and nothing else.
// The transport (cookie auth, single-flight 401 refresh-and-retry, structured
// error unwrapping, query-string building) lives in `./api` and is shared with
// the ecommerce bindings. This file used to carry a near-verbatim private copy
// of that machinery, whose error handling rendered an object `detail` as
// "[object Object]" and whose second refresh could race the first.

import { api, buildQs } from './api';
import type {
  Branch, BranchHoliday, BranchHolidayWrite, BusinessSettings, Charge, CostOfGoods, Device, DrawerOperation,
  InventoryCategory, InventoryItem, InventoryLevel, InventoryTransaction,
  InventoryValuation, KitchenFlow, PaymentMethod,
  PaymentReportRow, PermissionCatalogue, PosOrder, Printer, PurchaseOrder,
  Reason, Role, SalesBreakdownRow, SalesSummary, Staff,
  SupplierAnalysisRow, Supplier, Tag, Tax,
  TaxGroup, TaxReportRow, Till, Warehouse,
} from './pos-types';

// ─── Branches & floor plan ────────────────────────────────────────────────────

export const branchesApi = {
  list: (includeDeleted = false) => api.get<Branch[]>(`/branches${buildQs({ include_deleted: includeDeleted })}`),
  get: (id: string) => api.get<Branch>(`/branches/${id}`),
  create: (data: Partial<Branch>) => api.post<Branch>('/branches', data),
  update: (id: string, data: Partial<Branch>) => api.put<Branch>(`/branches/${id}`, data),
  remove: (id: string) => api.delete<void>(`/branches/${id}`),
  businessDays: (id: string, limit = 30) => api.get<unknown[]>(`/branches/${id}/business-days${buildQs({ limit })}`),
  closeBusinessDay: (id: string) => api.post<unknown>(`/branches/${id}/business-days/close`),
  sections: (id: string) => api.get<unknown[]>(`/branches/${id}/sections`),

  // ── Holidays ────────────────────────────────────────────────────────────
  //
  // Whole days this branch does not trade. Read by the delivery estimate, so
  // these are not decoration on a settings page: adding one moves what every
  // customer in this branch's zones is quoted from the next request onward.
  holidays: (id: string, includePast = false) =>
    api.get<BranchHoliday[]>(`/branches/${id}/holidays${buildQs({ include_past: includePast })}`),
  addHoliday: (id: string, data: BranchHolidayWrite) =>
    api.post<BranchHoliday>(`/branches/${id}/holidays`, data),
  updateHoliday: (holidayId: string, data: Partial<BranchHolidayWrite>) =>
    api.put<BranchHoliday>(`/branches/holidays/${holidayId}`, data),
  removeHoliday: (holidayId: string) =>
    api.delete<void>(`/branches/holidays/${holidayId}`),
};

// ─── Configuration ────────────────────────────────────────────────────────────

export const taxesApi = {
  list: () => api.get<Tax[]>('/taxes'),
  create: (d: Partial<Tax>) => api.post<Tax>('/taxes', d),
  update: (id: string, d: Partial<Tax>) => api.put<Tax>(`/taxes/${id}`, d),
  remove: (id: string) => api.delete<void>(`/taxes/${id}`),
};

export const taxGroupsApi = {
  list: () => api.get<TaxGroup[]>('/tax-groups'),
  create: (d: Record<string, unknown>) => api.post<TaxGroup>('/tax-groups', d),
  update: (id: string, d: Record<string, unknown>) => api.put<TaxGroup>(`/tax-groups/${id}`, d),
  remove: (id: string) => api.delete<void>(`/tax-groups/${id}`),
};

export const paymentMethodsApi = {
  list: () => api.get<PaymentMethod[]>('/payment-methods'),
  create: (d: Partial<PaymentMethod>) => api.post<PaymentMethod>('/payment-methods', d),
  update: (id: string, d: Partial<PaymentMethod>) => api.put<PaymentMethod>(`/payment-methods/${id}`, d),
  remove: (id: string) => api.delete<void>(`/payment-methods/${id}`),
};

export const chargesApi = {
  list: () => api.get<Charge[]>('/charges'),
  create: (d: Partial<Charge>) => api.post<Charge>('/charges', d),
  update: (id: string, d: Partial<Charge>) => api.put<Charge>(`/charges/${id}`, d),
  remove: (id: string) => api.delete<void>(`/charges/${id}`),
};

export const reasonsApi = {
  list: (type?: string) => api.get<Reason[]>(`/reasons${buildQs({ type })}`),
  create: (d: Partial<Reason>) => api.post<Reason>('/reasons', d),
  update: (id: string, d: Partial<Reason>) => api.put<Reason>(`/reasons/${id}`, d),
  remove: (id: string) => api.delete<void>(`/reasons/${id}`),
};

export const tagsApi = {
  list: (type?: string) => api.get<Tag[]>(`/tags${buildQs({ type })}`),
  create: (d: Partial<Tag>) => api.post<Tag>('/tags', d),
  update: (id: string, d: Partial<Tag>) => api.put<Tag>(`/tags/${id}`, d),
  remove: (id: string) => api.delete<void>(`/tags/${id}`),
};

export const kitchenFlowsApi = {
  list: (branchId?: string) => api.get<KitchenFlow[]>(`/kitchen-flows${buildQs({ branch_id: branchId })}`),
  create: (d: Record<string, unknown>) => api.post<KitchenFlow>('/kitchen-flows', d),
  update: (id: string, d: Record<string, unknown>) => api.put<KitchenFlow>(`/kitchen-flows/${id}`, d),
  remove: (id: string) => api.delete<void>(`/kitchen-flows/${id}`),
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
  remove: (id: string) => api.delete<void>(`/roles/${id}`),
  permissions: () => api.get<PermissionCatalogue>('/staff/permissions'),
};

export const staffApi = {
  list: (branchId?: string) => api.get<Staff[]>(`/staff${buildQs({ branch_id: branchId })}`),
  get: (id: string) => api.get<Staff>(`/staff/${id}`),
  create: (d: Record<string, unknown>) => api.post<Staff>('/staff', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Staff>(`/staff/${id}`, d),
  deactivate: (id: string) => api.delete<void>(`/staff/${id}`),
};

// ─── Devices & printers ───────────────────────────────────────────────────────

export const devicesApi = {
  list: (branchId?: string) => api.get<Device[]>(`/devices${buildQs({ branch_id: branchId })}`),
  create: (d: Record<string, unknown>) => api.post<Device>('/devices', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Device>(`/devices/${id}`, d),
  remove: (id: string) => api.delete<void>(`/devices/${id}`),
  issuePairingCode: (id: string) => api.post<Device>(`/devices/${id}/pairing-code`),
  unpair: (id: string) => api.post<Device>(`/devices/${id}/unpair`),
};

export const printersApi = {
  list: (params?: { branch_id?: string; device_id?: string; role?: string }) =>
    api.get<Printer[]>(`/printers${buildQs(params)}`),
  create: (d: Record<string, unknown>) => api.post<Printer>('/printers', d),
  update: (id: string, d: Record<string, unknown>) => api.put<Printer>(`/printers/${id}`, d),
  remove: (id: string) => api.delete<void>(`/printers/${id}`),
};

// ─── Tills ────────────────────────────────────────────────────────────────────

export const tillsApi = {
  list: (params?: { branch_id?: string; business_date?: string; status?: string }) =>
    api.get<Till[]>(`/tills${buildQs(params)}`),
  get: (id: string) => api.get<Till>(`/tills/${id}`),
  report: (id: string) => api.get<Record<string, unknown>>(`/tills/${id}/report`),
  drawerOperations: (id: string) => api.get<DrawerOperation[]>(`/tills/${id}/drawer-operations`),
};

// ─── POS orders ───────────────────────────────────────────────────────────────

export const posOrdersApi = {
  list: (params?: {
    branch_id?: string; business_date?: string; pos_status?: string;
    order_type?: string; open_only?: boolean; limit?: number;
  }) => api.get<PosOrder[]>(`/pos/orders${buildQs(params)}`),
  get: (id: string) => api.get<PosOrder>(`/pos/orders/${id}`),
  openChecks: (branchId: string) => api.get<PosOrder[]>(`/pos/kitchen/open-checks${buildQs({ branch_id: branchId })}`),
};

// ─── Inventory ────────────────────────────────────────────────────────────────

export const inventoryApi = {
  categories: () => api.get<InventoryCategory[]>('/inventory/categories'),
  createCategory: (d: Record<string, unknown>) => api.post<InventoryCategory>('/inventory/categories', d),
  updateCategory: (id: string, d: Record<string, unknown>) => api.put<InventoryCategory>(`/inventory/categories/${id}`, d),
  removeCategory: (id: string) => api.delete<void>(`/inventory/categories/${id}`),

  items: (params?: { search?: string; category_id?: string }) =>
    api.get<InventoryItem[]>(`/inventory/items${buildQs(params)}`),
  createItem: (d: Record<string, unknown>) => api.post<InventoryItem>('/inventory/items', d),
  updateItem: (id: string, d: Record<string, unknown>) => api.put<InventoryItem>(`/inventory/items/${id}`, d),
  removeItem: (id: string) => api.delete<void>(`/inventory/items/${id}`),

  levels: (params?: { branch_id?: string; warehouse_id?: string; below_minimum_only?: boolean }) =>
    api.get<InventoryLevel[]>(`/inventory/levels${buildQs(params)}`),

  warehouses: (branchId?: string) => api.get<Warehouse[]>(`/inventory/warehouses${buildQs({ branch_id: branchId })}`),
  createWarehouse: (d: Record<string, unknown>) => api.post<Warehouse>('/inventory/warehouses', d),

  suppliers: () => api.get<Supplier[]>('/inventory/suppliers'),
  createSupplier: (d: Record<string, unknown>) => api.post<Supplier>('/inventory/suppliers', d),
  updateSupplier: (id: string, d: Record<string, unknown>) => api.put<Supplier>(`/inventory/suppliers/${id}`, d),
  removeSupplier: (id: string) => api.delete<void>(`/inventory/suppliers/${id}`),

  transactions: (params?: { branch_id?: string; type?: string; status?: string; business_date?: string }) =>
    api.get<InventoryTransaction[]>(`/inventory/transactions${buildQs(params)}`),
  createTransaction: (d: Record<string, unknown>, post = true) =>
    api.post<InventoryTransaction>(`/inventory/transactions${buildQs({ post })}`, d),
  adjust: (d: Record<string, unknown>) => api.post<InventoryTransaction>('/inventory/transactions/adjust', d),

  purchaseOrders: (params?: { branch_id?: string; supplier_id?: string; status?: string }) =>
    api.get<PurchaseOrder[]>(`/inventory/purchase-orders${buildQs(params)}`),
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

export type DailySalesEmailResult = {
  subject: string;
  rows: number;
  sent: { recipient: string; status: string; error: string | null }[];
};

export const posReportsApi = {
  salesSummary: (w: Window) => api.get<SalesSummary>(`/pos/reports/sales/summary${buildQs(w)}`),
  salesBy: (dimension: string, w: Window, limit = 100) =>
    api.get<SalesBreakdownRow[]>(`/pos/reports/sales/by${buildQs({ dimension, limit, ...w })}`),
  payments: (w: Window) => api.get<PaymentReportRow[]>(`/pos/reports/payments${buildQs(w)}`),
  sendDailyEmail: (body: { date_from: string; date_to: string; recipients: string[] }) =>
    api.post<DailySalesEmailResult>('/pos/reports/sales/daily-email', body),
  suppliersAnalysis: (w: Window) =>
    api.get<SupplierAnalysisRow[]>(`/pos/reports/suppliers-analysis${buildQs(w)}`),
  taxes: (w: Window) => api.get<TaxReportRow[]>(`/pos/reports/taxes${buildQs(w)}`),
  voidsReturns: (w: Window) => api.get<Record<string, unknown>[]>(`/pos/reports/voids-returns${buildQs(w)}`),
  tills: (w: Window) => api.get<Record<string, unknown>[]>(`/pos/reports/tills${buildQs(w)}`),
  drawerOperations: (w: Window) => api.get<Record<string, unknown>[]>(`/pos/reports/drawer-operations${buildQs(w)}`),
  inventoryValuation: (branchId?: string) =>
    api.get<InventoryValuation>(`/pos/reports/inventory/valuation${buildQs({ branch_id: branchId })}`),
  costOfGoods: (w: Window) => api.get<CostOfGoods>(`/pos/reports/inventory/cost-of-goods${buildQs(w)}`),
};
