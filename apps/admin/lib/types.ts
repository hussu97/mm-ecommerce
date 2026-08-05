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
  refresh_token: string;
  token_type: string;
  user: User;
}

export interface AdminLoginOptions {
  email: string;
  is_admin: boolean;
  has_passkey: boolean;
  password_enabled: boolean;
  passkey_allowed: boolean;
  is_superadmin: boolean;
}

export interface AdminPasskey {
  id: string;
  name: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface AdminUserSummary {
  id: string;
  email: string;
  phone: string | null;
  is_active: boolean;
  is_superadmin: boolean;
  passkey_count: number;
  created_at: string;
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
  stock_quantity: number;
  image_urls: string[];
  is_active: boolean;
  is_featured: boolean;
  /**
   * Which channels sell this product. Empty means it is in the catalogue and
   * sold nowhere yet — a real state, not a missing value.
   */
  sales_channels: SalesChannel[];
  display_order: number;
  created_at: string;
  updated_at: string;
  product_modifiers: ProductModifier[];
  category: Category | null;
}

/** The order here is the order the console offers them in. */
export const SALES_CHANNELS = ['pos', 'web'] as const;

export type SalesChannel = (typeof SALES_CHANNELS)[number];

export const SALES_CHANNEL_LABELS: Record<SalesChannel, string> = {
  pos: 'POS',
  web: 'Website',
};

export interface ProductListResponse {
  items: Product[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export type OrderStatus =
  | 'created'
  | 'confirmed'
  | 'packed'
  // Set by the courier's own pickup event on an integrated zone, and by hand
  // everywhere else.
  | 'out_for_delivery'
  | 'delivered'
  // A rider reached the door and could not hand it over. Paid for and still
  // ours to deliver, so it leads back into the journey rather than ending it.
  | 'undelivered'
  | 'cancelled';

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

  // ── which channel, and what that channel needs shown ───────────────────────
  /** `online` for the storefront, `cashier` for the counter. */
  source?: string | null;
  /** The counter lifecycle — a different shape from `status`. */
  pos_status?: string | null;
  order_type?: string | null;
  branch_id?: string | null;
  /** The short number the counter calls out — "order 12". */
  check_number?: number | null;
  customer_name?: string | null;
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
/** One custom event the storefront tracks, and how often it fired. */
export interface EventCount { name: string; count: number; }
export interface TrafficData {
  visitors: number;
  sessions: number;
  pageviews: number;
  bounce_rate: number;
  avg_duration: number;
  pageviews_chart: PageviewPoint[];
  top_pages: TopPage[];
  events: EventCount[];
  configured: boolean;
  /** Set when Umami itself refused or could not be reached. */
  error: string | null;
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

/** Sales grouped by the delivery zone that priced each order. */
export interface ZoneSalesData { zone: string; orders: number; revenue: number; }

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

/**
 * One inbound courier webhook, as it arrived and as it was answered.
 *
 * Distinct from the events ledger, which only records what we accepted. This
 * covers everything that reached the URL — including the pushes that matched no
 * order, carried an unfamiliar key, or blew up inside the handler, which are
 * the ones worth looking at.
 */
export interface WebhookLog {
  id: string;
  provider: string;
  endpoint: string;
  received_at: string;
  remote_ip: string | null;
  /** Four characters either end of the key they sent. Never the key. */
  api_key_fingerprint: string | null;
  signature_valid: boolean | null;
  event_type: string | null;
  order_number: string | null;
  courier_order_id: string | null;
  /** Whether the push found an order at all. A run of `false` is a real problem. */
  matched: boolean | null;
  error: string | null;
  http_status: number | null;
  duration_ms: number | null;
}

/** The same row with its bodies, fetched one at a time. */
export interface WebhookLogDetail extends WebhookLog {
  payload: unknown;
  result: unknown;
}

export interface PaginatedWebhookLogs {
  items: WebhookLog[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface WebhookLogFacets {
  providers: string[];
  endpoints: string[];
  event_types: string[];
}

export interface PaginatedEmailLogs {
  items: EmailLog[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

/** Who carries an order out of the kitchen. */
export type FulfilmentProvider = 'lalamove' | 'noon_send' | 'third_party';

/**
 * Where a zone's fee comes from. `static` charges the zone's own published
 * price; `dynamic` charges the courier's quote for the customer's exact pin,
 * rounded up — and refuses the order outright when there is no quote.
 */
export type DeliveryPricingMode = 'static' | 'dynamic';

/** One zone on a delivery map: a shape, a price, and a courier. */
export interface DeliveryZone {
  id: string;
  name: string;
  /** Only charged when `pricing_mode` is `static`. */
  delivery_fee: number;
  /** The kitchen that bakes this zone's orders. Null = the default pickup branch. */
  branch_id: string | null;
  pricing_mode: DeliveryPricingMode;
  /**
   * Whether a qualifying basket delivers free here. Independent of the fee and
   * of the courier — a fixed-fee third-party zone is not automatically an
   * offer, and inferring it from either of those is how it last went wrong.
   */
  free_delivery_eligible: boolean;
  fulfilment_provider: FulfilmentProvider;
  display_order: number;
  point_count: number;
}

/**
 * A complete delivery map. Only one is live; the rest are drafts and history,
 * which is what makes a bad price a one-click rollback.
 */
export interface DeliveryMapVersion {
  id: string;
  name: string;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  activated_at: string | null;
  polygons: DeliveryZone[];
}

/** GeoJSON as the API hands it over — [lng, lat], MultiPolygon, holes after the outline. */
export interface ZoneGeometry {
  type: 'MultiPolygon';
  coordinates: number[][][][];
}

export interface DeliveryZoneShape {
  id: string;
  name: string;
  delivery_fee: number;
  pricing_mode: DeliveryPricingMode;
  free_delivery_eligible: boolean;
  fulfilment_provider: FulfilmentProvider;
  display_order: number;
  geometry: ZoneGeometry;
}

export interface DeliveryZoneMap {
  version: { id: string; name: string } | null;
  zones: DeliveryZoneShape[];
  bounds: { min_lat: number; max_lat: number; min_lng: number; max_lng: number } | null;
}

/**
 * A slot of the day whose orders travel together, in Dubai time.
 * End hour 24 means midnight closing the day.
 */
export interface BatchWindow {
  id: string;
  polygon_id: string;
  label: string;
  start_hour: number;
  start_minute: number;
  end_hour: number;
  end_minute: number;
  is_active: boolean;
  wraps_midnight: boolean;
}

export type BatchWindowWrite = Omit<BatchWindow, 'id' | 'polygon_id' | 'wraps_midnight'>;

/** One courier order carrying several of ours. */
export interface DeliveryBatch {
  id: string;
  polygon_id: string;
  zone_name: string | null;
  window_label: string | null;
  dispatch_at: string;
  status: 'pending' | 'dispatching' | 'dispatched' | 'failed' | 'cancelled';
  stop_count: number;
  courier_order_id: string | null;
  courier_status: string | null;
  share_link: string | null;
  driver_name: string | null;
  distance_m: number | null;
  cost_total: number | null;
  /** What the run worked out at per order — the number batching exists to move. */
  cost_per_delivery: number | null;
  dispatched_at: string | null;
  last_error: string | null;
  /** How many times this run has been offered to the courier. */
  attempt_count: number;
  /**
   * When it will be offered again on its own. Null means nothing more happens
   * without somebody pressing the button — it went out, or another attempt
   * cannot change the answer.
   */
  next_attempt_at: string | null;
  order_numbers: string[];
}

/** The live map, flattened, plus the settings that apply to every zone in it. */
export interface DeliveryZoneSummary {
  version: { id: string; name: string } | null;
  zones: Array<{
    name: string;
    /** Zero, and meaningless, when `pricing_mode` is `dynamic`. */
    delivery_fee: number;
    pricing_mode: DeliveryPricingMode;
    fulfilment_provider: FulfilmentProvider;
  }>;
  /** The same everywhere — free delivery does not depend on the zone. */
  free_threshold: number;
  default_delivery_fee: number;
  pickup_fee: number;
}

/**
 * The fulfilment side of an order. Admin-only — the storefront is never told
 * which courier is carrying the box.
 */
export interface OrderDelivery {
  provider: FulfilmentProvider;
  zone_name: string | null;
  fee_charged: number | null;
  quoted_cost: number | null;
  quoted_currency: string | null;
  quoted_distance_m: number | null;
  cost_total: number | null;
  /** Fee charged minus what the courier cost. Negative loses money. */
  margin: number | null;
  /** The seven digits the driver quotes. Null on a third-party zone. */
  courier_reference: string | null;
  courier_order_id: string | null;
  courier_status: string | null;
  share_link: string | null;
  driver_name: string | null;
  driver_phone: string | null;
  driver_plate: string | null;
  pod_status: string | null;
  pod_image_url: string | null;
  booked_at: string | null;
  picked_up_at: string | null;
  delivered_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  last_error: string | null;
  needs_attention: boolean;
}

export interface DeliverySettings {
  id: string;
  /** The same in every zone, on purpose. */
  free_delivery_threshold: number;
  pickup_fee: number;
  /** Charged when a pin falls outside every zone we have drawn. */
  default_delivery_fee: number;
}

// ─── Audit Logs ───────────────────────────────────────────────────────────────

export type AuditAction = 'CREATE' | 'UPDATE' | 'DELETE' | 'STATUS_CHANGE';

export interface AuditLog {
  id: string;
  action: AuditAction;
  entity_type: string;
  entity_id: string;
  entity_label: string;
  admin_id: string;
  admin_email: string;
  changes: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export interface PaginatedAuditLogs {
  items: AuditLog[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}
