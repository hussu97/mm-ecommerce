export interface User {
  id: string;
  email: string;
  phone: string | null;
  is_active: boolean;
  is_admin: boolean;
  is_guest: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Language {
  code: string;
  name: string;
  native_name: string;
  direction: 'ltr' | 'rtl';
  is_default: boolean;
  is_active: boolean;
  display_order: number;
}

export interface CmsPage {
  id: string;
  slug: string;
  is_active: boolean;
  content: Record<string, Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface UiTranslation {
  id: string;
  locale: string;
  namespace: string;
  key: string;
  value: string;
}

export interface ModifierOption {
  id: string;
  modifier_id: string;
  name: string;
  translations: Record<string, Record<string, string>>;
  sku: string;
  price: number;
  calories: number | null;
  is_active: boolean;
  display_order: number;
}

export interface Modifier {
  id: string;
  reference: string;
  name: string;
  translations: Record<string, Record<string, string>>;
  is_active: boolean;
  options: ModifierOption[];
}

export interface ProductModifier {
  id: string;
  modifier_id: string;
  modifier: Modifier;
  minimum_options: number;
  maximum_options: number;
  free_options: number;
  unique_options: boolean;
  display_order: number;
}

export interface Category {
  id: string;
  name: string;
  translations: Record<string, Record<string, string>>;
  slug: string;
  reference: string | null;
  description: string | null;
  image_url: string | null;
  display_order: number;
  is_active: boolean;
  product_count: number;
}

export interface Product {
  id: string;
  category_id: string | null;
  name: string;
  slug: string;
  sku: string | null;
  description: string | null;
  translations: Record<string, Record<string, string>>;
  base_price: number;
  calories: number | null;
  preparation_time: number | null;
  is_sold_by_weight: boolean;
  is_stock_product: boolean;
  image_urls: string[];
  is_active: boolean;
  is_featured: boolean;
  display_order: number;
  created_at: string;
  updated_at: string;
  product_modifiers: ProductModifier[];
  category: Category | null;
}

export interface ProductListResponse {
  items: Product[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export type OrderStatus = 'created' | 'confirmed' | 'packed' | 'cancelled';

export interface SelectedOptionSnapshot {
  modifier_id: string;
  modifier_name: string;
  option_id: string;
  option_name: string;
  option_price: number;
}

export interface OrderItem {
  id: string;
  product_id: string | null;
  product_name: string;
  product_sku: string;
  product_translations: Record<string, Record<string, string>>;
  quantity: number;
  base_price: number;
  options_price: number;
  unit_price: number;
  total_price: number;
  selected_options_snapshot: SelectedOptionSnapshot[];
}

export interface Order {
  id: string;
  order_number: string;
  user_id: string | null;
  email: string;
  delivery_method: 'delivery' | 'pickup';
  delivery_fee: number;
  subtotal: number;
  discount_amount: number;
  total: number;
  vat_rate: number;
  vat_amount: number;
  total_excl_vat: number;
  status: OrderStatus;
  promo_code_used: string | null;
  shipping_address_snapshot: Record<string, string> | null;
  payment_method: string | null;
  payment_provider: string | null;
  payment_id: string | null;
  notes: string | null;
  admin_notes: string | null;
  created_at: string;
  updated_at: string;
  items: OrderItem[];
  item_count?: number;
}

export interface PaginatedOrders {
  items: Order[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface PromoCode {
  id: string;
  code: string;
  discount_type: 'percentage' | 'fixed';
  discount_value: number;
  min_order_amount: number | null;
  max_uses: number | null;
  current_uses: number;
  is_active: boolean;
  valid_from: string | null;
  valid_until: string | null;
  created_at: string;
}

export interface UploadResponse {
  url: string;
  key: string;
}

// ─── Import ───────────────────────────────────────────────────────────────────

export interface ImportError {
  row: number;
  message: string;
}

export interface ImportResult {
  created: number;
  updated: number;
  skipped: number;
  errors: ImportError[];
}

// ─── Customers ────────────────────────────────────────────────────────────────

export interface CustomerSummary {
  id: string;
  email: string;
  phone: string | null;
  order_count: number;
  total_spent: number;
  created_at: string;
}

export interface PaginatedCustomers {
  items: CustomerSummary[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

// ─── Analytics ────────────────────────────────────────────────────────────────

export interface AnalyticsOverview {
  total_revenue: number;
  total_orders: number;
  avg_order_value: number;
  total_customers: number;
  revenue_growth: number;
  orders_growth: number;
}

export interface RevenuePoint {
  date: string;
  revenue: number;
}

export interface OrdersPoint {
  date: string;
  count: number;
}

export interface TopProduct {
  product_name: string;
  product_sku: string;
  revenue: number;
  quantity: number;
}

export interface FunnelData {
  created: number;
  confirmed: number;
  packed: number;
  cancelled: number;
  conversion_rate: number;
}

export interface PageviewPoint { date: string; views: number; }
export interface TopPage { path: string; views: number; }
export interface TrafficData {
  visitors: number;
  sessions: number;
  pageviews: number;
  bounce_rate: number;
  avg_duration: number;
  pageviews_chart: PageviewPoint[];
  top_pages: TopPage[];
  configured: boolean;
}

export interface CustomerBreakdown {
  registered: number;
  guest: number;
  new_customers: number;
  returning_customers: number;
}

export interface BreakdownItem { label: string; orders: number; revenue: number; }
export interface RevenueBreakdown {
  by_delivery_method: BreakdownItem[];
  by_payment_provider: BreakdownItem[];
}

export interface RegionData { region: string; orders: number; revenue: number; }

export interface PromoPerformance {
  code: string;
  uses: number;
  revenue_driven: number;
  discount_given: number;
}

// ─── Email Logs ───────────────────────────────────────────────────────────────

export type EmailLogStatus = 'sent' | 'failed' | 'skipped';

export interface EmailLog {
  id: string;
  template: string;
  recipient: string;
  subject: string;
  order_number: string | null;
  status: EmailLogStatus;
  resend_id: string | null;
  error: string | null;
  sent_at: string;
}

export interface PaginatedEmailLogs {
  items: EmailLog[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}
