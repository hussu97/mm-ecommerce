// ─── Language ─────────────────────────────────────────────────────────────────

export interface Language {
  code: string;
  name: string;
  native_name: string;
  direction: 'ltr' | 'rtl';
  is_default: boolean;
  is_active: boolean;
  display_order: number;
}

// ─── Modifier ─────────────────────────────────────────────────────────────────

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

// ─── Product ──────────────────────────────────────────────────────────────────

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

// ─── Cart ─────────────────────────────────────────────────────────────────────

export interface SelectedOptionSnapshot {
  modifier_id: string;
  modifier_name: string;
  modifier_translations?: Record<string, Record<string, string>>;
  option_id: string;
  option_name: string;
  option_translations?: Record<string, Record<string, string>>;
  option_price: number;
}

export interface CartItem {
  id: string;
  cart_id: string;
  product_id: string;
  quantity: number;
  selected_options: SelectedOptionSnapshot[];
  created_at: string;
  product_name: string | null;
  product_image: string | null;
  product_translations?: Record<string, Record<string, string>>;
  unit_price: number | null;
  line_total: number | null;
}

export interface Cart {
  id: string;
  user_id: string | null;
  session_id: string | null;
  items: CartItem[];
  item_count: number;
  subtotal: number;
}

// ─── Order ────────────────────────────────────────────────────────────────────

export type OrderStatus =
  | 'created'
  | 'confirmed'
  | 'packed'
  | 'out_for_delivery'
  | 'delivered'
  // A rider reached the door and could not hand it over. Not settled: the
  // order is paid for and a second attempt is the usual answer.
  | 'undelivered'
  | 'cancelled'
  | 'payment_failed'
  | 'refunded'
  | 'disputed';

/**
 * A place a customer can collect from.
 *
 * Both locales ride on one object because the API does not guess which one to
 * serve — the same payload feeds an English page, an Arabic page and an email
 * with no locale at all.
 */
export interface PickupBranch {
  id: string;
  reference: string;
  name: string;
  name_ar: string | null;
  address: string | null;
  address_ar: string | null;
  city: string | null;
  city_ar: string | null;
  phone: string | null;
  latitude: number | null;
  longitude: number | null;
  /** Google Maps, built from the branch's own pin rather than its address text. */
  maps_url: string | null;
  opening_from: string;
  opening_to: string;
}

/** Where an order is in the journey. See `fulfilment_service.Fulfilment`. */
export type FulfilmentStage =
  | 'preparing'
  | 'ready'
  | 'on_the_way'
  | 'collected'
  | 'delivered'
  | 'undelivered'
  | 'settled';

/**
 * How an order reaches its customer.
 *
 * Deliberately narrow: no courier name, no driver, no cost. The API keeps that
 * on a separate admin-only model, and this is the whole of what the storefront
 * is told.
 */
export interface Fulfilment {
  method: DeliveryMethod;
  stage: FulfilmentStage;
  /** When to expect it. Null when there is nothing honest to promise. */
  estimated_at: string | null;
  /** `time` — an hour; `day` — a date only; `exact` — it already happened. */
  precision: 'time' | 'day' | 'exact' | null;
  /** The courier's live map, once a rider is actually carrying the order. */
  tracking_url: string | null;
  /** Whether a rider we hear back from carries this — decides what we promise. */
  courier_managed: boolean;
  packed_at: string | null;
  picked_up_at: string | null;
  delivered_at: string | null;
  /** The pickup branch. Null for delivery orders. */
  branch: PickupBranch | null;
}
export type DeliveryMethod = 'delivery' | 'pickup';

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
  delivery_method: DeliveryMethod;
  delivery_fee: number;
  /**
   * The small-basket surcharge this order was written with. Zero on pickup and
   * on anything above the threshold.
   *
   * Read rather than recomputed wherever an existing order is being shown — a
   * customer coming back to pay for one owes what it was priced at, not what
   * today's settings would charge.
   */
  low_order_fee?: number;
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
  /**
   * Whether this order's email already belongs to a real (non-guest) account.
   * Decides whether the confirmation page offers to create one or to sign in.
   */
  email_has_account?: boolean;
  /** When it arrives, where to collect it, and whether there is a rider to watch. */
  fulfilment?: Fulfilment | null;
  /** The language it was placed in. */
  locale?: string;
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

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

// ─── Promo ────────────────────────────────────────────────────────────────────

export interface PromoValidateResponse {
  valid: boolean;
  discount_amount: number;
  message: string | null;
  /**
   * True when an unverified phone is the only thing between this customer and
   * the discount — i.e. there is a button that fixes it, rather than a refusal
   * they can do nothing about.
   */
  requires_phone_verification?: boolean;
}

/**
 * The new-customer coupon the shop is currently advertising, as the storefront
 * is allowed to see it.
 *
 * Every number here is the coupon row's own. Nothing about the offer is written
 * into the browser, so changing the percentage, the cap or the number of orders
 * in the admin changes every surface that quotes them at once — which is the
 * only way a tray, a checkout line and an email can be trusted to agree.
 */
export interface AdvertisedPromo {
  code: string;
  /** The same coupon spelled in Arabic, where it has one. */
  code_ar: string | null;
  discount_type: 'percentage' | 'fixed';
  discount_value: number;
  /** The ceiling on one order's discount, in AED. Null means uncapped. */
  max_discount_amount: number | null;
  /** How many of a customer's first orders it covers. */
  first_orders_limit: number | null;
  requires_phone_verification: boolean;
}

// ─── Address ──────────────────────────────────────────────────────────────────

export interface Address {
  id: string;
  user_id: string;
  label: string;
  first_name: string;
  last_name: string;
  phone: string;
  address_line_1: string;
  address_line_2: string | null;
  unit_number: string | null;
  country: string;
  is_default: boolean;
  latitude: number | null;
  longitude: number | null;
  created_at: string;
}

export interface AddressCreate {
  label?: string;
  first_name: string;
  last_name: string;
  phone: string;
  address_line_1: string;
  address_line_2?: string;
  unit_number?: string;
  country?: string;
  is_default?: boolean;
  latitude?: number | null;
  longitude?: number | null;
}

// ─── Order (create) ───────────────────────────────────────────────────────────

export interface OrderCreate {
  /** Optional — a phone number is enough to run a delivery. */
  email?: string;
  delivery_method: DeliveryMethod;
  shipping_address?: AddressCreate;
  /** Which branch the customer is coming to. Pickup orders only. */
  pickup_branch_id?: string;
  /** The checkout's language. Every email about this order is written in it. */
  locale?: string;
  promo_code?: string;
  payment_method: string;
  notes?: string;
  session_id?: string;
}

/** What the public track lookup returns for an order number + email. */
export interface TrackResult {
  order_number: string;
  status: OrderStatus;
  delivery_method: DeliveryMethod;
  items_count: number;
  created_at: string;
  total: number;
  fulfilment: Fulfilment | null;
}

// ─── Blog ─────────────────────────────────────────────────────────────────────

export interface BlogPostContent {
  title?: string;
  excerpt?: string;
  body?: string;
  cover_image?: string;
  meta_description?: string;
  tags?: string[];
}

export interface BlogPost {
  slug: string;
  content: BlogPostContent;
  created_at: string;
  updated_at: string;
}

export interface BlogPostListResponse {
  items: BlogPost[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

// ─── Delivery ─────────────────────────────────────────────────────────────────

/**
 * The delivery numbers that hold before a pin exists.
 *
 * Deliberately not a list of areas and prices: the fee comes from where the
 * marker lands, and a table here would invite guessing one from the address
 * text — which is the guess the zone map replaced.
 */
export interface DeliveryRates {
  /**
   * No `free_threshold` and no `default_delivery_fee`. Both were national
   * numbers answering what every zone now answers for itself, so quoting them
   * before there is a pin was quoting a figure true in none of the fifteen
   * zones. The threshold arrives with the quote, once an address exists.
   */
  pickup_fee: number;
  /** The small-basket surcharge. Zero switches it off. */
  low_order_fee: number;
  /**
   * The basket at or below which the surcharge applies, inclusive.
   * Null also switches it off — distinct from zero, which would mean
   * "free above nothing".
   */
  low_order_threshold: number | null;
}

// ─── Payment ──────────────────────────────────────────────────────────────────

/**
 * What the customer chooses. Not who processes it.
 *
 * There is one card option on the checkout and there always was — the customer
 * never had an opinion about Stripe. Which processor settles the card is chosen
 * on the server from the `payment_gateways` table, so it can be changed during
 * an incident without shipping anything, and it is deliberately not a value
 * this app can name.
 */
export type PaymentMethod = 'card' | 'cod';

/**
 * A stored `payment_method`, read as one of the two things it can mean.
 *
 * Orders written before methods and gateways were separated carry `stripe`
 * here, and the retry path feeds exactly that value back in — so a customer
 * retrying last week's failed payment would otherwise send a word this app no
 * longer has a branch for. Anything unrecognised is a card, which is the only
 * option available for delivery and the historical default.
 */
export function toPaymentMethod(value: string | null | undefined): PaymentMethod {
  return value?.toLowerCase() === 'cod' ? 'cod' : 'card';
}

/**
 * The word to put on the wire for a payment method.
 *
 * `card` internally, `stripe` over HTTP — for one release only, and for a
 * specific reason: **the web and the API deploy independently and in parallel.**
 * `deploy-web` goes to Vercel and needs only `lint-web`; `deploy-gcp` goes to
 * the VM behind migrations and health checks, and takes minutes longer. The web
 * is therefore live against the *previous* API for the whole of that window.
 *
 * The previous API validates `payment_method` against an enum of
 * `{stripe, cod, tabby, tamara}` and resolves a payment provider by exact
 * string. Sending it `card` fails twice over — a 422 on order creation, then a
 * "Unknown payment provider 'card'" on the session — so every card checkout
 * would fail from the moment the frontend shipped until the API caught up.
 *
 * `stripe` is understood by both: the old API takes it literally, the new one
 * normalises it to `card` (`payment_methods.normalise_method`), which is the
 * legacy-alias path that exists for exactly this. Nothing is stored as `stripe`
 * either way — the API writes `card`.
 *
 * **Safe to delete once the API carrying `card` is deployed.** It buys nothing
 * after that, and leaving it is how a gateway name lives on in a client that
 * has no business naming one.
 */
export function toWireMethod(method: PaymentMethod): 'stripe' | 'cod' {
  return method === 'cod' ? 'cod' : 'stripe';
}

export interface PaymentSessionResponse {
  /**
   * The gateway that was actually used — `stripe`, `ziina`, `cod`, `none`.
   * Reported so a failure can be attributed to a processor in analytics; it is
   * never shown to the customer.
   */
  provider: string;
  session_id: string | null;
  checkout_url: string | null;
  confirmed?: boolean;
}

/** Result of pricing delivery against the active zone map. */
export interface DeliveryQuote {
  /**
   * What the customer pays — already zero when free delivery applies.
   *
   * Null when there is nothing to price: no pin yet, or a pin nothing can be
   * delivered to. `serviceable` is what tells those two apart.
   */
  delivery_fee: number | null;
  /** What it would have cost, so the total can be struck through. */
  base_fee: number | null;
  free_delivery_applied: boolean;
  /**
   * Whether free delivery reaches this pin at all. False in the areas priced
   * from a live courier quote — there is no fee of ours to waive there, so an
   * upsell towards the threshold would be advertising something that will not
   * happen.
   */
  free_delivery_available: boolean;
  /** Null until a pin resolves to a zone — there is no national number now. */
  free_threshold: number | null;
  /**
   * True while the threshold above is the national default standing in for a
   * zone we have not resolved yet — no pin dropped. Copy driven by it has to
   * stay hedged until it turns false.
   */
  free_threshold_provisional?: boolean;
  remaining_for_free: number;
  zone_name: string | null;
  in_known_zone: boolean;
  /**
   * When the order should arrive. Null until there is a pin to read a schedule
   * off. `precision` is `"time"` where the schedule is ours — the order joins a
   * run whose departure we set — and `"day"` where the van belongs to a partner
   * and an hour would be a promise we cannot keep.
   */
  delivery_estimate: { at: string; precision: 'time' | 'day' } | null;
  /**
   * False when we cannot deliver to this pin at all. The order endpoint refuses
   * it too, so blocking the button here is a courtesy rather than the guard.
   */
  serviceable: boolean;
}

/**
 * What delivery looks like at a point, before there is a basket.
 *
 * From `GET /delivery/area`. Names a place and a speed, never a carrier.
 */
export interface DeliveryArea {
  serviceable: boolean;
  zone_name: string | null;
  /** Null where the fee is a live courier quote and needs a basket to exist. */
  delivery_fee: number | null;
  /** The basket that earns free delivery *here*. Zero is real, not "unset". */
  free_threshold: number | null;
  free_delivery_available: boolean;
  /** `express` is inside the hour, `same_day` today, `next_day` tomorrow. */
  speed: 'express' | 'same_day' | 'next_day';
}
