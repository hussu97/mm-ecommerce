# Database Schema — ER Diagram

Melting Moments Ecommerce — PostgreSQL 16

Generated from SQLAlchemy models in `apps/api/app/models/`.

```
┌─────────────────────────────────────────────────────────────────────┐
│  USERS                                                              │
│  id (PK, UUID)                                                      │
│  email (unique, indexed)                                            │
│  hashed_password (nullable)                                         │
│  phone (nullable)                                                   │
│  is_active, is_admin, is_guest                                      │
│  created_at, updated_at                                             │
└───────┬────────────┬───────────────┬─────────────────┬─────────────┘
        │            │               │                 │
        │1           │1              │1                │1
        │*           │*              │*                │*
┌───────▼──────┐ ┌───▼──────────┐ ┌─▼────────────┐ ┌─▼──────────────┐
│  ADDRESSES   │ │  CARTS       │ │  ORDERS      │ │  REFRESH_TOKENS │
│  id (PK)     │ │  id (PK)     │ │  id (PK)     │ │  id (PK)       │
│  user_id(FK) │ │  user_id(FK) │ │  user_id(FK) │ │  user_id(FK)   │
│  label       │ │  session_id  │ │  order_number│ │  token_hash    │
│  first_name  │ │    (indexed) │ │    (unique)  │ │  expires_at    │
│  last_name   │ │  created_at  │ │  email       │ │  is_revoked    │
│  phone       │ │  updated_at  │ │  status      │ │  created_at    │
│  address_l1  │ │              │ │  delivery_   │ └────────────────┘
│  address_l2  │ │              │ │    method    │
│  region      │ └──────┬───────┘ │  delivery_   │
│  country     │        │1        │    fee       │
│  is_default  │        │*        │  subtotal    │
│  latitude    │ ┌──────▼───────┐ │  discount_   │
│  longitude   │ │  CART_ITEMS  │ │    amount    │
│  created_at  │ │  id (PK)     │ │  total       │
└──────────────┘ │  cart_id(FK) │ │  vat_rate    │
                 │  product_id  │ │  vat_amount  │
                 │    (FK)      │ │  total_excl_ │
                 │  quantity    │ │    vat       │
                 │  selected_   │ │  promo_code_ │
                 │    options   │ │    used      │
                 │    (JSONB)   │ │  shipping_   │
                 │  created_at  │ │    address_  │
                 └──────┬───────┘ │    snapshot  │
                        │         │    (JSONB)   │
                        │         │  payment_    │
                        │         │    method    │
                        │         │  payment_    │
                        │         │    provider  │
                        │         │  payment_id  │
                        │         │  notes       │
                        │         │  created_at  │
                        │         └──────┬───────┘
                        │                │1
                        │                │*
                        │         ┌──────▼───────┐
                        │         │  ORDER_ITEMS │
                        │         │  id (PK)     │
                        │         │  order_id(FK)│
                        │         │  product_id  │
                        │         │    (FK,      │
                        │         │    nullable) │
                        │         │  product_    │
                        │         │    name      │
                        │         │    (snapshot)│
                        │         │  product_sku │
                        │         │    (snapshot)│
                        │         │  product_    │
                        │         │  translations│
                        │         │    (JSONB)   │
                        │         │  quantity    │
                        │         │  base_price  │
                        │         │  options_    │
                        │         │    price     │
                        │         │  unit_price  │
                        │         │  total_price │
                        │         │  selected_   │
                        │         │    options_  │
                        │         │    snapshot  │
                        │         │    (JSONB)   │
                        │         └──────────────┘
                        │
                 ┌──────▼──────────────────────────────────────────────┐
                 │  PRODUCTS                                           │
                 │  id (PK, UUID)                                      │
                 │  category_id (FK → categories, nullable)            │
                 │  name, slug (unique), sku (unique, nullable)        │
                 │  description, translations (JSONB)                  │
                 │  base_price, cost (nullable)                        │
                 │  barcode, calories, preparation_time                │
                 │  image_urls (ARRAY[String])                         │
                 │  is_active, is_featured, is_sold_by_weight          │
                 │  is_stock_product, display_order                    │
                 │  created_at, updated_at                             │
                 └────────────┬────────────────────────────────────────┘
                              │1
                              │*
                 ┌────────────▼──────────────┐
                 │  PRODUCT_MODIFIERS        │
                 │  id (PK)                  │
                 │  product_id (FK)          │
                 │  modifier_id (FK)         │  ← unique(product_id, modifier_id)
                 │  minimum_options          │
                 │  maximum_options          │
                 │  free_options             │
                 │  unique_options           │
                 │  display_order            │
                 └────────────┬──────────────┘
                              │*
                              │1
                 ┌────────────▼──────────────┐
                 │  MODIFIERS                │
                 │  id (PK)                  │
                 │  reference (unique)       │
                 │  name, translations(JSONB)│
                 │  is_active                │
                 │  created_at, updated_at   │
                 └────────────┬──────────────┘
                              │1
                              │*
                 ┌────────────▼──────────────┐
                 │  MODIFIER_OPTIONS         │
                 │  id (PK)                  │
                 │  modifier_id (FK)         │
                 │  name, translations(JSONB)│
                 │  sku (unique)             │
                 │  price, cost              │
                 │  calories                 │
                 │  is_active, display_order │
                 └───────────────────────────┘


┌───────────────────────────────────────┐  ┌──────────────────────────────────────┐
│  CATEGORIES                           │  │  PROMO_CODES                         │
│  id (PK)                              │  │  id (PK)                             │
│  name, translations (JSONB)           │  │  code (unique, indexed)              │
│  slug (unique), reference (unique)    │  │  discount_type (percentage | fixed)  │
│  description, image_url               │  │  discount_value                      │
│  display_order, is_active             │  │  min_order_amount (nullable)         │
│  created_at, updated_at               │  │  max_uses (nullable), current_uses   │
└───────────────────────────────────────┘  │  is_active, valid_from, valid_until  │
                                           │  created_at                          │
                                           └──────────────────────────────────────┘
```

## Relationships summary

| Table | FK → | On delete |
|-------|------|-----------|
| `addresses.user_id` | `users.id` | CASCADE |
| `carts.user_id` | `users.id` | SET NULL |
| `cart_items.cart_id` | `carts.id` | CASCADE |
| `cart_items.product_id` | `products.id` | CASCADE |
| `orders.user_id` | `users.id` | SET NULL |
| `order_items.order_id` | `orders.id` | CASCADE |
| `order_items.product_id` | `products.id` | SET NULL |
| `products.category_id` | `categories.id` | SET NULL |
| `product_modifiers.product_id` | `products.id` | CASCADE |
| `product_modifiers.modifier_id` | `modifiers.id` | CASCADE |
| `modifier_options.modifier_id` | `modifiers.id` | CASCADE |
| `refresh_tokens.user_id` | `users.id` | CASCADE |

## Indexes

| Table | Index | Type | Notes |
|-------|-------|------|-------|
| `users` | `email` | btree unique | login lookup |
| `carts` | `user_id` | btree | |
| `carts` | `session_id` | btree | guest cart lookup |
| `cart_items` | `cart_id` | btree | |
| `cart_items` | `(cart_id, product_id)` | btree composite | add-to-cart dedup |
| `orders` | `order_number` | btree unique | |
| `orders` | `user_id` | btree | |
| `orders` | `email` | btree | |
| `orders` | `status` | btree | admin filtering |
| `order_items` | `order_id` | btree | |
| `products` | `slug` | btree unique | |
| `products` | `sku` | btree unique | |
| `products` | `category_id` | btree | |
| `categories` | `slug` | btree unique | |
| `categories` | `reference` | btree unique | |
| `modifiers` | `reference` | btree unique | |
| `modifier_options` | `sku` | btree unique | |
| `modifier_options` | `modifier_id` | btree | |
| `product_modifiers` | `product_id` | btree | |
| `product_modifiers` | `modifier_id` | btree | |
| `addresses` | `user_id` | btree | |
| `refresh_tokens` | `user_id` | btree | |
| `refresh_tokens` | `token_hash` | btree unique | |
| `refresh_tokens` | `(user_id, expires_at)` WHERE `is_revoked=false` | partial btree | active token lookup |
| `promo_codes` | `code` | btree unique | |

## Notes

- `order_items` stores product name/sku/translations as **snapshots** at order time — product edits don't mutate historical orders.
- `orders.shipping_address_snapshot` (JSONB) stores the full address at order time — same reason.
- `carts.session_id` supports **guest checkout**: anonymous users have a cart keyed by browser session ID. When they check out, the backend falls back to session_id if no user-owned cart is found.
- `users.is_guest = true` marks accounts created solely for checkout — they have no password and are treated as unauthenticated by the storefront.
- `promo_codes` is standalone (no FK to orders); the used code is stored as a string in `orders.promo_code_used`.
