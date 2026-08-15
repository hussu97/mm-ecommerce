// Types for the POS domain. Kept separate from `types.ts` so the original
// storefront/admin surface stays easy to read as the POS side grows.

export type Translations = Record<string, Record<string, string>>;

export type OrderType = 'dine_in' | 'pickup' | 'delivery' | 'drive_thru';
export type PosOrderStatus =
  | 'draft' | 'pending' | 'active' | 'closed'
  | 'declined' | 'void' | 'returned' | 'joined';

export const ORDER_TYPE_LABELS: Record<OrderType, string> = {
  dine_in: 'Dine In',
  pickup: 'Takeaway',
  delivery: 'Delivery',
  drive_thru: 'Drive Thru',
};

// ─── Configuration ────────────────────────────────────────────────────────────

export interface Branch {
  id: string;
  name: string;
  name_localized: string | null;
  translations: Translations;
  reference: string;
  type: 'restaurant' | 'kitchen' | 'warehouse';
  latitude: number | null;
  longitude: number | null;
  phone: string | null;
  address: string | null;
  address_localized: string | null;
  /** The city, as a customer would name it. Shown on the collection picker. */
  city: string | null;
  city_localized: string | null;
  opening_from: string;
  opening_to: string;
  business_day_start: string;
  inventory_end_of_day_time: string;
  receipt_header: string | null;
  receipt_footer: string | null;
  tax_number: string | null;
  tax_registration_name: string | null;
  tax_group_id: string | null;
  /** What noon Send calls this branch. Null = it cannot dispatch through them. */
  noon_send_outlet_code: string | null;
  /** Optional address revision for that outlet, `addr::…::CODE::2`. */
  noon_send_outlet_address_code: string | null;
  receives_online_orders: boolean;
  /** Whether customers may choose to collect from here. Not implied by the flag above. */
  offers_pickup: boolean;
  accepts_reservations: boolean;
  reservation_duration: number;
  is_active: boolean;
  display_order: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Tax {
  id: string;
  name: string;
  name_localized: string | null;
  rate: number;
  type: 'inclusive' | 'exclusive';
  is_active: boolean;
  deleted_at: string | null;
}

export interface TaxGroup {
  id: string;
  name: string;
  reference: string | null;
  is_active: boolean;
  is_default: boolean;
  taxes: Tax[];
  combined_rate: number;
  deleted_at: string | null;
}

export type PaymentMethodType = 'cash' | 'card' | 'online' | 'other';

export interface PaymentMethod {
  id: string;
  name: string;
  code: string;
  type: PaymentMethodType;
  auto_open_drawer: boolean;
  allows_tendering: boolean;
  allows_tips: boolean;
  allows_refund: boolean;
  is_active: boolean;
  display_order: number;
  deleted_at: string | null;
}

export interface Charge {
  id: string;
  name: string;
  reference: string | null;
  type: 'percentage' | 'fixed' | 'open';
  value: number;
  is_auto_applied: boolean;
  order_types: string[];
  tax_group_id: string | null;
  is_active: boolean;
  deleted_at: string | null;
}

export type ReasonType = 'void_return' | 'quantity_adjustment' | 'drawer_operation';

export interface Reason {
  id: string;
  name: string;
  type: ReasonType;
  is_active: boolean;
  deleted_at: string | null;
}

export type TagType = 'order' | 'customer' | 'product' | 'inventory_item' | 'revenue_center';

export interface Tag {
  id: string;
  name: string;
  type: TagType;
  color: string | null;
  deleted_at: string | null;
}

// ─── Staff ────────────────────────────────────────────────────────────────────

export interface PermissionEntry {
  slug: string;
  description: string;
}

export interface PermissionCatalogue {
  groups: Record<string, PermissionEntry[]>;
}

export interface Role {
  id: string;
  name: string;
  permissions: string[];
  is_super_admin: boolean;
  is_active: boolean;
  user_count: number;
  deleted_at: string | null;
}

export interface Staff {
  id: string;
  email: string;
  display_name: string | null;
  staff_number: string | null;
  phone: string | null;
  role_id: string | null;
  role_name: string | null;
  branch_ids: string[];
  is_active: boolean;
  is_admin: boolean;
  is_staff: boolean;
  is_driver: boolean;
  has_pin: boolean;
}

// ─── Devices ──────────────────────────────────────────────────────────────────

export type DeviceType = 'cashier' | 'sub_cashier' | 'display' | 'notifier';

export interface Device {
  id: string;
  name: string;
  reference: string;
  type: DeviceType;
  branch_id: string;
  status: 'available' | 'used' | 'disabled';
  pairing_code: string | null;
  pairing_code_expires_at: string | null;
  last_seen_at: string | null;
  app_version: string | null;
  model_identifier: string | null;
  /**
   * Takes those orders and prints them without anyone pressing Accept. For a
   * kitchen with nobody watching the iPad; a counter terminal leaves it off.
   */
  auto_accept_online_orders: boolean;
  deleted_at: string | null;
}

export interface Printer {
  id: string;
  name: string;
  branch_id: string;
  device_id: string | null;
  role: 'receipt' | 'kitchen' | 'label' | 'report';
  connection: 'lan' | 'bluetooth' | 'usb' | 'airprint' | 'cloud';
  ip_address: string | null;
  port: number;
  paper_width_mm: number;
  characters_per_line: number;
  codepage: string;
  supports_arabic: boolean;
  cut_after_print: boolean;
  has_cash_drawer: boolean;
  copies: number;
  kitchen_flow_id: string | null;
  is_default: boolean;
  is_active: boolean;
  deleted_at: string | null;
}

export interface KitchenFlow {
  id: string;
  branch_id: string;
  name: string;
  order_types: string[];
  category_ids: string[];
  is_default: boolean;
  auto_complete_seconds: number;
  display_order: number;
  is_active: boolean;
  deleted_at: string | null;
}

// ─── Tills ────────────────────────────────────────────────────────────────────

export interface Till {
  id: string;
  branch_id: string;
  device_id: string | null;
  user_id: string;
  business_date: string;
  status: 'open' | 'closed';
  opened_at: string;
  closed_at: string | null;
  opening_amount: number;
  closing_amount: number | null;
  estimated_cash: number;
  variance: number;
  totals: Record<string, unknown>;
}

export interface DrawerOperation {
  id: string;
  till_id: string;
  type: 'pay_in' | 'pay_out' | 'cash_drop' | 'open_drawer' | 'sales' | 'return';
  amount: number;
  notes: string | null;
  recorded_at: string;
}

// ─── POS orders ───────────────────────────────────────────────────────────────

export interface PosOrderItem {
  id: string;
  product_name: string;
  product_sku: string;
  quantity: number;
  returned_quantity: number;
  unit_price: number;
  total_price: number;
  discount_amount: number;
  tax_amount: number;
  status: string | null;
  kitchen_notes: string | null;
}

export interface PosOrder {
  id: string;
  order_number: string;
  check_number: number | null;
  branch_id: string | null;
  order_type: OrderType | null;
  source: string | null;
  pos_status: PosOrderStatus | null;
  business_date: string | null;
  guests: number;
  customer_name: string | null;
  customer_phone: string | null;
  subtotal: number;
  discount_amount: number;
  charges_amount: number;
  vat_amount: number;
  total_excl_vat: number;
  rounding_amount: number;
  tips_amount: number;
  total: number;
  amount_paid: number;
  balance_due: number;
  opened_at: string | null;
  closed_at: string | null;
  items: PosOrderItem[];
}

// ─── Inventory ────────────────────────────────────────────────────────────────

export interface InventoryCategory {
  id: string;
  name: string;
  reference: string | null;
  parent_id: string | null;
  display_order: number;
  is_active: boolean;
  deleted_at: string | null;
}

export interface Warehouse {
  id: string;
  branch_id: string;
  name: string;
  reference: string | null;
  is_default: boolean;
  is_active: boolean;
}

export interface InventoryItem {
  id: string;
  sku: string;
  barcode: string | null;
  name: string;
  category_id: string | null;
  storage_unit: string;
  ingredient_unit: string;
  storage_to_ingredient_factor: number;
  minimum_level: number;
  maximum_level: number;
  par_level: number;
  cost: number;
  costing_method: 'fixed' | 'from_ingredients';
  yield_percentage: number;
  is_product: boolean;
  is_active: boolean;
  deleted_at: string | null;
}

export interface InventoryLevel {
  id: string;
  item_id: string;
  warehouse_id: string;
  quantity: number;
  average_cost: number;
  last_counted_at: string | null;
  item_name: string | null;
  item_sku: string | null;
  ingredient_unit: string | null;
  minimum_level: number | null;
  par_level: number | null;
  total_value: number | null;
  is_below_minimum: boolean;
}

export interface Supplier {
  id: string;
  name: string;
  reference: string | null;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  payment_terms_days: number;
  is_active: boolean;
  deleted_at: string | null;
}

export type PurchaseOrderStatus =
  | 'draft' | 'pending' | 'approved' | 'declined' | 'partially_received' | 'closed';

export interface PurchaseOrderItem {
  id: string;
  item_id: string;
  quantity: number;
  received_quantity: number;
  outstanding_quantity: number;
  unit: string;
  unit_cost: number;
  total_cost: number;
  item_name: string | null;
  item_sku: string | null;
}

export interface PurchaseOrder {
  id: string;
  reference: string;
  status: PurchaseOrderStatus;
  supplier_id: string;
  supplier_name: string | null;
  branch_id: string;
  business_date: string;
  delivery_date: string | null;
  total_cost: number;
  items: PurchaseOrderItem[];
  created_at: string;
}

export interface InventoryTransaction {
  id: string;
  reference: string;
  type: string;
  status: string;
  branch_id: string;
  business_date: string;
  total_cost: number;
  notes: string | null;
  posted_at: string | null;
  created_at: string;
  items: Array<{
    id: string;
    item_id: string;
    quantity: number;
    unit: string;
    unit_cost: number;
    total_cost: number;
    item_name: string | null;
    item_sku: string | null;
  }>;
}

// ─── Reports ──────────────────────────────────────────────────────────────────

export interface SalesSummary {
  orders_count: number;
  gross_sales: number;
  discounts: number;
  charges: number;
  returns: number;
  taxes: number;
  net_sales_excl_tax: number;
  rounding: number;
  tips: number;
  net_sales: number;
  average_order_value: number;
  voided_orders: number;
  voided_value: number;
}

export interface SalesBreakdownRow {
  key: string;
  label: string;
  orders?: number;
  quantity?: number;
  net_sales: number;
  discounts: number;
}

export interface PaymentReportRow {
  payment_method_id: string;
  name: string;
  type: string;
  transactions: number;
  amount: number;
  refunds: number;
  net: number;
  tips: number;
}

export interface TaxReportRow {
  name: string;
  rate: number;
  rate_percent: number;
  taxable_amount: number;
  tax_amount: number;
}

export interface InventoryValuation {
  items_tracked: number;
  total_value: number;
  below_minimum_count: number;
  below_minimum: Array<{
    item_id: string;
    sku: string;
    name: string;
    quantity: number;
    minimum_level: number;
    par_level: number;
    unit: string;
    shortfall: number;
  }>;
}

export interface CostOfGoods {
  cost_of_goods: number;
  net_sales_excl_tax: number;
  gross_margin: number;
  gross_margin_percent: number;
}

export interface MenuEngineeringRow extends SalesBreakdownRow {
  cost: number;
  margin: number;
  margin_percent: number;
  classification: 'star' | 'plough_horse' | 'puzzle' | 'dog';
}

export interface BusinessSettings {
  id: string;
  business_name: string;
  currency_code: string;
  currency_symbol: string;
  decimal_places: number;
  timezone: string;
  receipt_header: string | null;
  receipt_footer: string | null;
  invoice_title: string;
  receipt_show_order_number: boolean;
  receipt_show_calories: boolean;
  receipt_show_subtotal: boolean;
  receipt_show_rounding: boolean;
  receipt_show_closer_username: boolean;
  receipt_show_creator_username: boolean;
  receipt_show_check_number: boolean;
  receipt_hide_free_modifiers: boolean;
  receipt_show_pickup_phone: boolean;
  receipt_show_qr: boolean;
  kitchen_sorting: 'as_added' | 'by_category';
  kitchen_show_default_modifiers: boolean;
  kitchen_auto_print_on_send: boolean;
  prevent_negative_stock: boolean;
  require_customer_for_delivery: boolean;
  default_order_type: OrderType;
  cash_rounding_step: number;
  auto_logout_seconds: number;
  order_number_reset_daily: boolean;
  enable_tips: boolean;
}


/** Kitchen timings over the window; each average covers only the tickets
 *  that reached that stage, so one still cooking does not skew prep time. */
export interface SpeedOfServiceReport {
  tickets: number;
  acknowledged: number;
  completed: number;
  outstanding: number;
  avg_acknowledge_minutes: number;
  avg_prep_minutes: number;
  avg_total_minutes: number;
  slowest_ticket_minutes: number;
}

export interface BranchTrendRow {
  branch: string;
  business_date: string;
  orders: number;
  net_sales: number;
  average_order_value: number;
}

export interface TableUtilizationRow {
  section: string;
  table: string;
  seats: number;
  turns: number;
  covers: number;
  net_sales: number;
  average_minutes: number;
  sales_per_seat: number;
}

export interface SupplierAnalysisRow {
  supplier: string;
  purchase_orders: number;
  total_spend: number;
}
