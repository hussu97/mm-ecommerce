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

export type OrderStatus = 'created' | 'confirmed' | 'packed' | 'cancelled' | 'payment_failed';
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
}

// ─── Address ──────────────────────────────────────────────────────────────────

export type RegionCode =
  | 'dubai'
  | 'sharjah'
  | 'ajman'
  | 'abu_dhabi'
  | 'fujairah'
  | 'ras_al_khaimah'
  | 'umm_al_quwain'
  | 'al_ain'
  | 'rest_of_uae';

export interface Address {
  id: string;
  user_id: string;
  label: string;
  first_name: string;
  last_name: string;
  phone: string;
  address_line_1: string;
  address_line_2: string | null;
  region: RegionCode;
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
  region: RegionCode;
  country?: string;
  is_default?: boolean;
  latitude?: number | null;
  longitude?: number | null;
}

// ─── Order (create) ───────────────────────────────────────────────────────────

export interface OrderCreate {
  email: string;
  delivery_method: DeliveryMethod;
  shipping_address?: AddressCreate;
  promo_code?: string;
  payment_method: string;
  notes?: string;
  session_id?: string;
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

export interface PublicRegion {
  slug: string;
  name_translations: Record<string, string>;
  delivery_fee: number;
}

export interface DeliveryRates {
  regions: PublicRegion[];
  free_threshold: number;
  pickup_fee: number;
}

// ─── Payment ──────────────────────────────────────────────────────────────────

export interface PaymentSessionResponse {
  provider: string;
  session_id: string | null;
  checkout_url: string | null;
  confirmed?: boolean;
}
