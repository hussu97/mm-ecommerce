// ─── Product ──────────────────────────────────────────────────────────────────

export interface ProductVariant {
  id: string;
  product_id: string;
  name: string;
  sku: string;
  price: number;
  stock_quantity: number;
  is_active: boolean;
  display_order: number;
}

export interface Category {
  id: string;
  name: string;
  slug: string;
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
  description: string | null;
  base_price: number;
  image_urls: string[];
  is_active: boolean;
  is_featured: boolean;
  display_order: number;
  created_at: string;
  updated_at: string;
  variants: ProductVariant[];
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

export interface CartItem {
  id: string;
  cart_id: string;
  variant_id: string;
  quantity: number;
  created_at: string;
  variant: ProductVariant | null;
  product_name: string | null;
  product_image: string | null;
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

export type OrderStatus = 'created' | 'confirmed' | 'packed' | 'cancelled';
export type DeliveryMethod = 'delivery' | 'pickup';

export interface OrderItem {
  id: string;
  variant_id: string | null;
  product_name: string;
  variant_name: string;
  quantity: number;
  unit_price: number;
  total_price: number;
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
  first_name: string;
  last_name: string;
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

export type EmirateEnum =
  | 'Dubai'
  | 'Sharjah'
  | 'Ajman'
  | 'Abu Dhabi'
  | 'Ras Al Khaimah'
  | 'Fujairah'
  | 'Umm Al Quwain';

export interface Address {
  id: string;
  user_id: string;
  label: string;
  first_name: string;
  last_name: string;
  phone: string;
  address_line_1: string;
  address_line_2: string | null;
  city: string;
  emirate: EmirateEnum;
  country: string;
  is_default: boolean;
  created_at: string;
}

export interface AddressCreate {
  label?: string;
  first_name: string;
  last_name: string;
  phone: string;
  address_line_1: string;
  address_line_2?: string;
  city: string;
  emirate: EmirateEnum;
  country?: string;
  is_default?: boolean;
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

// ─── Payment ──────────────────────────────────────────────────────────────────

export interface PaymentSessionResponse {
  provider: string;
  session_id: string;
  checkout_url: string;
}
