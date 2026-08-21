export interface User {
  id: string;
  email: string;
  phone: string | null;
  is_active: boolean;
  is_admin: boolean;
  is_guest: boolean;
  created_at: string;
}

/**
 * What `/auth/login`, `/auth/register` and the passkey login answer with.
 *
 * The two token fields are real and nothing here reads them: the console
 * authenticates by httpOnly cookie, which `_set_auth_cookies` sets from these
 * same values server-side before the body is serialised. They are declared
 * because the API sends them, not because a caller wants them — a type that
 * quietly omits a field the wire carries is the drift this codebase spent an
 * audit removing.
 *
 * Worth a separate look: because they are in the body, the tokens are also in
 * JavaScript's reach, which is most of what the httpOnly cookie was chosen to
 * prevent. Dropping them from the response of the three cookie-based endpoints
 * would cost nothing here or in the storefront — neither reads them — but the
 * register's bearer flow needs its own token in its own body, so this is a
 * change to the web/admin endpoints only, and one to make deliberately rather
 * than in passing.
 */
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
  /** Offered in the basket's add-on tray. */
  is_cart_addon?: boolean;
  /** What this product asks to have written on it; null asks for nothing. */
  personalisation_type?: 'handwritten_note' | null;
  personalisation_max_length?: number;
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
  // On the register and, on a zone we dispatch ourselves, with a courier
  // already booked. The same minute as `confirmed` for most zones and hours
  // later for a batched one, which is why it is a step of its own.
  | 'arrived_at_pos'
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
  /**
   * How many of this option are on the line — five fudge brownies in a box of
   * six, not one.
   *
   * Optional because it is: rows written before option quantities existed
   * repeated the option instead and carry no key, so a missing value means one.
   * Mirrors the same default in `schemas/option_snapshot.OptionSnapshot`.
   */
  quantity?: number;
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
  /**
   * What the customer asked to have written on this line, verbatim.
   *
   * Distinct from `kitchen_notes`: that one is typed by a cashier. This is the
   * one string on the order that has to be reproduced exactly, spelling
   * included, because somebody is about to copy it onto a card.
   */
  personalisation_note?: string | null;
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
  /**
   * The stamps and the promise, as the customer-facing order page already sees
   * them. Built by `fulfilment_service`, so the admin and the customer are
   * looking at one answer rather than two derivations of it.
   */
  fulfilment: OrderFulfilment | null;
  /**
   * When this order reached each status, oldest first, one entry per status.
   *
   * The timeline's only source. It used to read `created_at` for step one and
   * borrow the rest from `order_deliveries`, which only an integrated courier
   * ever fills — so a pickup order, a third-party zone or anything walked
   * through by hand showed one stamp and four blanks.
   */
  status_history: OrderStatusStamp[];
  payment_method: string | null;
  payment_provider: string | null;
  payment_id: string | null;
  /** How much has been sent back to the card. Zero on almost every order. */
  refunded_amount: number;
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

/** When an order reached one status. */
export interface OrderStatusStamp {
  status: OrderStatus;
  at: string;
}

/** What `fulfilment_service` knows about where an order has got to. */
export interface OrderFulfilment {
  stage: string;
  /** When it should arrive. Null once there is nothing left to promise. */
  estimated_at: string | null;
  /**
   * `time` when an hour can be named, `day`/`day_by` when only the date is
   * ours — a third party's van is not on our schedule. `exact` is a stamp of
   * something that already happened.
   */
  precision: string | null;
  packed_at: string | null;
  picked_up_at: string | null;
  delivered_at: string | null;
}

export interface PromoCode {
  id: string;
  code: string;
  /** The same coupon in Arabic — a second name for one row, not a second coupon. */
  code_ar: string | null;
  discount_type: 'percentage' | 'fixed';
  discount_value: number;
  min_order_amount: number | null;
  /** Ceiling on what a percentage can take off, in AED. Meaningless on a fixed amount. */
  max_discount_amount: number | null;
  /** Restricts the code to a customer's first N orders. */
  first_orders_limit: number | null;
  /**
   * Requires a proved mobile number before this code will apply.
   *
   * What makes `first_orders_limit` worth having. That rule counts identities —
   * account, email, phone — and the first two are free to mint; a phone number
   * is the only one in the checkout that costs anything to fake.
   */
  requires_phone_verification: boolean;
  max_uses: number | null;
  /** How many times one customer may redeem this code. */
  max_uses_per_user: number | null;
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
  /** How customers chose to pay: `card` or `cod`. The commercial split. */
  by_payment_method: BreakdownItem[];
  /**
   * Which processor settled the card orders: `stripe` or `ziina`. Card only —
   * cash has no gateway, and a `cod` slice here would make the chart answer
   * neither question.
   */
  by_payment_gateway: BreakdownItem[];
  /** @deprecated The old combined split. Now identical to `by_payment_method`. */
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

// ─── Live baskets ─────────────────────────────────────────────────────────────

/** One line of a basket somebody is still holding. */
export interface LiveCartLine {
  product_id: string;
  product_name: string;
  product_sku: string | null;
  quantity: number;
  unit_price: number;
  line_total: number;
  /** The chosen options as words, e.g. `Filling: Ferrero ×2`. */
  options: string[];
  personalisation_note: string | null;
}

/**
 * A basket that currently holds items.
 *
 * Every figure is the server's — see `/analytics/live-carts`. Nothing on this
 * screen recomputes a price, and `estimated_total` deliberately excludes any
 * promo discount: the code is stored on the basket, the discount is decided at
 * the checkout, and quoting one here would be a second answer.
 */
export interface LiveCart {
  id: string;
  user_id: string | null;
  session_id: string | null;
  email: string | null;
  /** `account` | `checkout` — where the address above came from. */
  email_source: string | null;
  is_registered: boolean;
  lines: LiveCartLine[];
  item_count: number;
  subtotal: number;
  low_order_fee: number;
  delivery_fee: number | null;
  delivery_zone: string | null;
  estimated_total: number;
  promo_code: string | null;
  created_at: string;
  last_activity_at: string | null;
  idle_minutes: number | null;
}

/** The header figures — `reachable_value` against `total_value` is the case. */
export interface LiveCartsSummary {
  carts: number;
  total_value: number;
  with_email: number;
  reachable_value: number;
  idle_over_1h: number;
  idle_over_24h: number;
}

export interface PaginatedLiveCarts {
  items: LiveCart[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
  summary: LiveCartsSummary;
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
  /** Their id for whatever the push was about: a courier booking, a payment intent. */
  external_id: string | null;
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
  /**
   * The basket that earns free delivery *here*.
   *
   * Per zone, because one national number was simultaneously too high for a
   * bike run inside Sharjah at AED 13 and too low for a car to Jebel Ali at
   * AED 59. Zero is a real value and means free at any basket — Sharjah — and
   * is not the same as the zone making no offer, which is what
   * `free_delivery_eligible` says.
   */
  free_delivery_threshold: number;
  /**
   * The courier this zone's orders are priced and dispatched against.
   *
   * The *preferred* one. `alternate_providers` is what makes that word mean
   * something.
   */
  fulfilment_provider: FulfilmentProvider;
  /**
   * Where an order in this zone may be *moved* when the preferred courier will
   * not carry it — a booking nobody picks up, a fleet that has gone quiet.
   *
   * Deliberately not "every other courier". noon Send cannot cross an emirate
   * boundary or carry a run past 20 km, so a Dubai zone lists a third party and
   * not them.
   */
  alternate_providers: FulfilmentProvider[];
  /**
   * The run this zone travels on, or null for one that leaves alone.
   *
   * Read-only — only a migration attaches a zone to a group. Shown because
   * changing the courier detaches it: a run is one booking with one courier.
   */
  batch_group_id: string | null;
  display_order: number;
  point_count: number;
}

/** One courier an order could be moved to, and whether it actually can. */
export interface FulfilmentTarget {
  provider: FulfilmentProvider;
  available: boolean;
  /** Why not, in words for the person reading them. Null when available. */
  reason: string | null;
}

/**
 * Whether calling off the current booking is likely to cost anything.
 *
 * Carries no figure on purpose: neither courier quotes a cancellation fee over
 * an API, and a number worked out from a published tariff would sit next to
 * real quoted costs and read as one of them.
 */
export interface CancellationExposure {
  will_be_charged: boolean;
  reason: string;
}

/** Where this order may go, and what stands in the way. */
export interface FulfilmentOptions {
  current: FulfilmentProvider;
  /** What the zone chose. The gap between this and `current` is the interesting part. */
  preferred: FulfilmentProvider;
  targets: FulfilmentTarget[];
  /** An order-level refusal, which blocks every target at once. */
  blocked: string | null;
  /** The one refusal a person can clear, by abandoning the booking in the way. */
  must_abandon_first: boolean;
  exposure: CancellationExposure | null;
}

/** What moving this order to one courier would cost **us**. */
export interface FulfilmentQuote {
  provider: FulfilmentProvider;
  /** Null for a third party, whom we neither book nor price. */
  cost: number | null;
  currency: string | null;
  distance_m: number | null;
  /**
   * Lalamove issue one and will only book against it. noon Send price from a
   * rate card, so this is null — which is not an error.
   */
  quotation_id: string | null;
  expires_at: string | null;
  fee_charged: number | null;
  margin: number | null;
  cancels_booking: string | null;
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
  /**
   * The basket that earns free delivery *here*.
   *
   * Per zone, because one national number was simultaneously too high for a
   * bike run inside Sharjah at AED 13 and too low for a car to Jebel Ali at
   * AED 59. Zero is a real value and means free at any basket — Sharjah — and
   * is not the same as the zone making no offer, which is what
   * `free_delivery_eligible` says.
   */
  free_delivery_threshold: number;
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
  /** Windows belong to a group, not a zone — see `BatchGroup`. */
  group_id: string;
  label: string;
  start_hour: number;
  start_minute: number;
  end_hour: number;
  end_minute: number;
  is_active: boolean;
  wraps_midnight: boolean;
}

export type BatchWindowWrite = Omit<BatchWindow, 'id' | 'group_id' | 'wraps_midnight'>;

/**
 * A set of zones whose orders ride together, and the schedule they share.
 *
 * Which zones share a courier booking used to fall out of two schedules
 * coincidentally ending on the same minute — nobody declared it and nothing
 * displayed it. It is a row now, so this screen can show it.
 */
export interface BatchGroup {
  id: string;
  name: string;
  courier_code: string;
  /** Minutes from the van leaving to the last drop. Dubai 90, northern 120. */
  delivery_minutes_after_dispatch: number;
  is_active: boolean;
  zone_names: string[];
  windows: BatchWindow[];
}

/**
 * A carrier, and the two numbers that decide what a zone of its own is quoted.
 *
 * Only one of them is ever read: `unbatched_promise_kind` says which. A courier
 * we dispatch ourselves leaves when we say so, and the honest answer is minutes
 * from ready; one that collects on its own schedule is one we cannot see, and
 * the only thing we can commit to is a number of days.
 *
 * These apply when there is no batch group to wait for — which is every noon
 * Send zone and every third-party one, and any Lalamove zone left off a
 * schedule.
 */
export interface Courier {
  code: string;
  name: string;
  /** Read-only here. Whether this carrier can appear on the Batching screen. */
  supports_batching: boolean;
  unbatched_promise_kind: 'minutes' | 'next_day';
  unbatched_promise_minutes: number | null;
  unbatched_promise_days: number;
  is_active: boolean;
  /** Zones on the live map currently carried by this courier. */
  zone_count: number;
}

export type CourierWrite = Partial<
  Pick<
    Courier,
    | 'unbatched_promise_kind'
    | 'unbatched_promise_minutes'
    | 'unbatched_promise_days'
    | 'is_active'
  >
>;

/** One courier order carrying several of ours. */
export interface DeliveryBatch {
  id: string;
  group_id: string;
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
  /**
   * Who the zone originally chose, when that is no longer who is carrying it.
   * Null on all but an order an admin moved onto Lalamove by hand.
   */
  original_provider: FulfilmentProvider | null;
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
  /** When the current driver took the order. */
  driver_assigned_at: string | null;
  /**
   * Which driver this is — 1 for the first, 2 for whoever took over from them.
   * Zero until a courier matches anybody.
   */
  driver_assignment_count: number;
  /**
   * Everyone who held this order before the current driver, oldest first. Empty
   * on the overwhelming majority, which are carried by the driver they started
   * with.
   */
  previous_drivers: PreviousDriver[];
  /**
   * Roughly how far the driver still is from the branch, in kilometres, with
   * the moment that position was true.
   *
   * Null unless a named driver is on the way *to the kitchen*: after collection
   * the number would only grow, and a position the courier has stopped
   * refreshing is withheld rather than shown stale. An estimate — say so
   * wherever it is rendered.
   */
  driver_distance_km: number | null;
  /**
   * Minutes of driving against live traffic. Null where only the straight-line
   * estimate exists — never derived from the distance, because a duration got
   * by dividing a guess by an assumed speed is a guess wearing the clothes of a
   * measurement.
   */
  driver_eta_minutes: number | null;
  driver_location_at: string | null;
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

/** A driver who used to be on this order, and when they stopped being. */
export interface PreviousDriver {
  sequence: number;
  name: string | null;
  phone: string | null;
  plate: string | null;
  assigned_at: string | null;
  replaced_at: string | null;
}

/** What Lalamove would charge to carry a third-party order, and the margin. */
export interface LalamoveQuote {
  quotation_id: string;
  cost: number;
  currency: string;
  distance_m: number | null;
  /** Five minutes out. Past it the booking is refused and we re-quote. */
  expires_at: string | null;
  /** What the customer paid. Assigning does not change it. */
  fee_charged: number | null;
  /** `fee_charged - cost`. Negative means this delivery loses money. */
  margin: number | null;
}

/** One transition, with who caused it. Admin only. */
export interface OrderStatusEvent {
  status: OrderStatus;
  previous_status: OrderStatus | null;
  at: string;
  /** `checkout | admin | pos | payment | courier | system | backfill` */
  source: string;
  actor_label: string | null;
  note: string | null;
}

export interface DeliverySettings {
  id: string;
  /** The same in every zone, on purpose. */
  free_delivery_threshold: number;
  pickup_fee: number;
  /** Charged when a pin falls outside every zone we have drawn. */
  default_delivery_fee: number;
  /** The small-basket surcharge. Delivery only, never pickup. */
  low_order_fee: number;
  /**
   * The basket at or below which the surcharge applies, inclusive.
   * Null switches the fee off — distinct from zero, which would charge it on
   * everything.
   */
  low_order_threshold: number | null;
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

// ─── Payment Gateways ─────────────────────────────────────────────────────────

/**
 * A card processor and the terms on which it is sent traffic.
 *
 * The customer never sees any of this. They chose "card"; which of Stripe or
 * Ziina settles it is an operations decision, and this is the shape of it.
 */
export interface PaymentGateway {
  code: string;
  name: string;
  /** Whether an operator has switched it on. */
  is_active: boolean;
  /** Lower goes first — both when choosing and when falling back. */
  priority: number;
  /**
   * Whether it may be reached for *automatically* after another gateway failed.
   *
   * Not the same question as `is_active`. A processor with worse rates is a
   * fine deliberate choice and a bad reflex.
   */
  supports_failover: boolean;
  min_amount: number | null;
  max_amount: number | null;
  /** Sends sandbox payments that take test cards and charge nothing. */
  test_mode: boolean;
  /**
   * Whether this *environment* holds credentials for it.
   *
   * Computed on the server, never stored — it is a fact about the running
   * container. A gateway that is not configured cannot be activated, which is
   * what lets the Ziina toggle exist on production and do nothing.
   */
  is_configured: boolean;
  /** Whether the router would actually pick it right now. */
  is_routable: boolean;
}

export interface PaymentGatewayUpdate {
  is_active?: boolean;
  priority?: number;
  supports_failover?: boolean;
  min_amount?: number | null;
  max_amount?: number | null;
  test_mode?: boolean;
}

/**
 * What the shop kept on one order. **Admin only.**
 *
 * Its own type rather than fields on `Order`, mirroring the API: what a courier
 * cost and what a processor keeps are not the customer's business, and the
 * separation is what keeps that structural rather than a rule to remember.
 */
export interface OrderEconomics {
  charged: number;
  items_value: number;
  /** Null on a third-party zone — nobody invoices us per order there. */
  courier_cost: number | null;
  processing_fee: number;
  /** Whether the fee is the gateway's rate or its invoice. */
  processing_fee_is_estimated: boolean;
  refunded: number;
  net: number;
  /** Null when the base is zero — a full-discount order has no percentage. */
  margin_on_charged: number | null;
  margin_on_items: number | null;
}

// ─── Per-branch availability ──────────────────────────────────────────────────

/**
 * One branch's override of the catalogue, for one product.
 *
 * These tables are exception-only: a row exists where a branch differs, and
 * "no row" means "sells it as the catalogue says". That is why the console
 * indexes them by `product_id + branch_id` and treats a miss as in stock —
 * reading absence as unavailable would show every branch as out of everything.
 */
export interface BranchProductAvailability {
  branch_id: string;
  product_id: string;
  is_in_stock: boolean;
  is_active: boolean;
  price: number | null;
  /**
   * When it comes back, or null for "until somebody puts it back".
   *
   * Only meaningful while `is_in_stock` is false: putting something back clears
   * the clock as well as the flag, and a CHECK constraint refuses a row that is
   * available and still counting down.
   */
  out_of_stock_until: string | null;
}

/**
 * How long a stockout lasts. The same three the register offers, because they
 * are the same three answers a shop actually gives — and a console that
 * invented a fourth would be describing a state no terminal can produce.
 */
export type StockDuration = 'one_hour' | 'end_of_day' | 'indefinite';

export const STOCK_DURATIONS: { value: StockDuration; label: string }[] = [
  { value: 'one_hour', label: 'For an hour' },
  { value: 'end_of_day', label: 'Until tomorrow' },
  { value: 'indefinite', label: 'Until I put it back' },
];

/**
 * One old URL and where it goes now.
 *
 * `from_path` and `to_path` are stored without a locale prefix — `/cat-brownies`,
 * not `/en/cat-brownies` — and apply to both languages, because a slug rename
 * breaks both at once. A rule that genuinely concerns one language can still be
 * written out in full, and the storefront tries that exact form first.
 */
export interface UrlRedirect {
  id: string;
  from_path: string;
  to_path: string;
  /** Whether everything below `from_path` moves with it — see the model docstring. */
  is_prefix: boolean;
  status_code: 301 | 308;
  is_active: boolean;
  /** 'manual' | 'category_rename' | 'seed'. Renaming a category writes its own. */
  source: string;
  note: string | null;
  hit_count: number;
  last_hit_at: string | null;
  created_at: string;
  updated_at: string;
}
