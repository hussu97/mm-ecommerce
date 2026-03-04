# Melting Moments Ecommerce - Build Tracker

## ✅ Prompt 1: Project Scaffolding & Monorepo Setup — DONE
- [x] Initialize Turborepo with pnpm workspaces (root package.json, pnpm-workspace.yaml, turbo.json)
- [x] Create Next.js 15 `apps/web` (App Router, TypeScript, Tailwind CSS v4)
  - [x] Tailwind theme: primary=#8a5a64, secondary=#d6acab, tertiary=#dfbdc1, + surface/text/border tokens
  - [x] Fonts: Playfair Display (400, 400i, 600) + Jost (300, 400, 500, 600) via next/font
  - [x] Material Icons import
  - [x] Dark mode: class-based toggle (`@custom-variant dark`)
- [x] Create Next.js 15 `apps/admin` (same Tailwind config)
- [x] Create FastAPI `apps/api`
  - [x] pyproject.toml with all deps (fastapi, sqlalchemy, alembic, stripe, resend, etc.)
  - [x] App structure: api/v1/, models/, schemas/, services/, core/ (config, security, deps), main.py
  - [x] Alembic init (env.py + script.py.mako)
  - [x] .env.example
- [x] Create `packages/ui` with basic component exports
- [x] Create `packages/types` with placeholder types
- [x] Create `packages/config` with shared ESLint, TypeScript configs
- [x] docker-compose.yml with PostgreSQL 16 + healthcheck
- [x] Root .gitignore (Node, Python, IDE, .env)
- [x] Copy branding assets: logos → apps/web/public/images/logos/, photos → apps/web/public/images/photos/
- [x] Create favicon from branding/logos/favicon_logo.jpeg
- [x] **Verify**: `pnpm install` ✅ succeeded (320 packages), both apps build ✅

## ✅ Prompt 2: Database Models & Migrations — DONE
- [x] Base model class (UUIDMixin + TimestampMixin in models/base.py)
- [x] User model (email, hashed_password nullable, first/last name, phone, is_active/admin/guest)
- [x] Address model (user FK, label, full address fields, EmirateEnum, is_default)
- [x] Category model (name, slug unique, description, image_url, display_order, is_active)
- [x] Product model (category FK, name, slug unique, description, base_price, image_urls ARRAY, is_featured)
- [x] ProductVariant model (product FK, name, sku unique, price, stock_quantity)
- [x] Cart + CartItem models (user/session support for guests)
- [x] Order model (order_number "MM-YYYYMMDD-XXX", status enum, delivery/payment fields, address snapshot JSONB)
- [x] OrderItem model (snapshots of product/variant names at order time)
- [x] PromoCode model (percentage/fixed, min_order, max_uses, valid dates)
- [x] Database session setup (async engine + session factory) in core/database.py
- [x] Alembic env.py configured for async (from Prompt 1)
- [x] Initial migration: alembic/versions/001_initial_tables.py (10 tables + 4 enums)
- [x] Seed script (scripts/seed_db.py):
  - [x] 5 categories (Brownies, Cookies, Cookie Melt, Mix Boxes, Desserts)
  - [x] 6/5/3/3/3 products per category with realistic names/prices + variants
  - [x] 1 admin user (admin@meltingmomentscakes.com)
  - [x] 2 promo codes: MM10 (10% off), FREESHIP (free shipping)
- [x] Pydantic schemas for all models (Create, Update, Response) across 7 schema files
- [x] **Verify**: all 10 tables register ✅, all schemas import + instantiate ✅, migration syntax ✅
  - Note: DB test skipped (Docker not running) — run `docker compose up -d postgres && alembic upgrade head && python -m scripts.seed_db` to fully verify

## ✅ Prompt 3: Backend Core - Auth, Config & Middleware — DONE
- [x] Config (pydantic-settings): DATABASE_URL, SECRET_KEY, CORS origins, all service keys, APP_ENV
- [x] Security: JWT (access + reset tokens via python-jose), bcrypt hashing (direct, not passlib)
- [x] Dependencies: get_db, get_current_user, get_current_active_user, get_admin_user, get_optional_user
- [x] Auth API (7 routes):
  - [x] POST /auth/register — creates user + returns JWT
  - [x] POST /auth/login — email/password, returns JWT
  - [x] POST /auth/guest — creates guest user, returns JWT
  - [x] GET /auth/me, PUT /auth/me — profile read/update
  - [x] POST /auth/forgot-password — stateless JWT reset token (email stub for Prompt 7)
  - [x] POST /auth/reset-password — validates reset token, updates password
- [x] Middleware: CORS, RequestIDMiddleware (X-Request-ID), LoggingMiddleware (method/path/status/ms)
- [x] Main app: 12 routes, /api/v1 prefix, startup/shutdown events, /health endpoint
- [x] Error handling: 6 custom exceptions (NotFound/BadRequest/Unauthorized/Forbidden/Conflict/Unprocessable) + global handlers
- [x] **Verify**: all 7 auth routes present ✅, JWT encode/decode ✅, bcrypt hash/verify ✅
  - Note: Swagger UI verify deferred until Docker is up (`uvicorn app.main:app --reload`)

## ✅ Prompt 4: Backend API - Products, Cart & Categories — DONE
- [x] Categories API: GET /categories (with product count), GET /{slug}, POST/PUT/DELETE (admin)
- [x] Products API:
  - [x] GET /products (filters: category, search, featured, sort, pagination)
  - [x] GET /products/{slug} (with variants)
  - [x] GET /products/featured
  - [x] POST/PUT/DELETE (admin)
  - [x] Variant CRUD (admin)
- [x] Cart API:
  - [x] GET /cart (by user_id or session_id)
  - [x] POST /cart/items, PUT /cart/items/{id}, DELETE /cart/items/{id}
  - [x] DELETE /cart (clear)
  - [x] POST /cart/merge (guest → user after login)
  - [x] Calculate subtotal, item count, check stock
- [x] Service layer: product_service, cart_service, category_service
- [x] Image upload API: POST/DELETE /uploads/image (Cloudflare R2, validate type/size)
- [x] **Verify**: all 28 routes registered ✅, all modules import cleanly ✅
  - Note: Live Swagger test deferred until Docker is up (`docker compose up -d postgres && uvicorn app.main:app --reload`)

## ✅ Prompt 5: Backend API - Orders, Delivery & Promo Codes — DONE
- [x] Delivery service:
  - [x] Dubai/Sharjah/Ajman → 35 AED, rest of UAE → 50 AED, pickup → free
  - [x] Free shipping if subtotal >= 200 AED
  - [x] GET /delivery/rates, POST /delivery/calculate
- [x] Promo Code API:
  - [x] POST /promo-codes/validate (check active, dates, max_uses, min_order)
  - [x] Admin CRUD
- [x] Orders API:
  - [x] POST /orders (validate stock → calc totals → apply promo → calc delivery → create → clear cart)
  - [x] Order number format: "MM-YYYYMMDD-XXX"
  - [x] GET /orders (user's orders, paginated)
  - [x] GET /orders/{order_number}
  - [x] PUT /orders/{order_number}/status (admin, validated transitions)
  - [x] GET /orders/admin/all (admin, filters by status + search)
- [x] Order service: create_order, update_status, calculate totals (5% VAT baked into prices)
- [x] Address API: GET, POST, PUT, DELETE, PUT /{id}/default
- [x] **Verify**: 47 routes registered ✅, all modules import cleanly ✅
  - Note: Live test deferred until Docker is up

## ✅ Prompt 6: Backend API - Payments (Stripe + Tabby + Tamara) — DONE
- [x] Payment service with provider registry pattern (stripe/tabby/tamara dispatch)
- [x] StripePaymentProvider:
  - [x] Stripe Checkout Session (AED, line items, delivery fee line item, metadata, success/cancel URLs)
  - [x] Cards support (apple_pay auto-enabled via Stripe Dashboard)
  - [x] Discount applied via Stripe Coupon when promo code used
- [x] TabbyPaymentProvider (stub with full TODO docs + API reference)
- [x] TamaraPaymentProvider (stub with full TODO docs + API reference)
- [x] Payments API:
  - [x] POST /payments/create-session (order_number, provider) — idempotent via idempotency_key
  - [x] POST /payments/webhooks/stripe (signature verification, checkout.session.completed/expired)
  - [x] POST /payments/webhooks/tabby (stub, always 200)
  - [x] POST /payments/webhooks/tamara (stub, always 200)
  - [x] GET /payments/{order_number}/status
- [x] Security: Stripe signature verification (HMAC), idempotency keys on session creation, structured audit logging
- [x] **Verify**: 57 total routes registered ✅, all imports clean ✅
  - Note: End-to-end Stripe test requires STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET in .env

## ✅ Prompt 7: Backend API - Email Service — DONE
- [x] Email service (Resend v2): _send base with RESEND_API_KEY guard + error logging, send_order_confirmation/cancelled/packed, send_welcome, send_password_reset
- [x] Jinja2 email templates (mobile-responsive, branded):
  - [x] Base template (logo #8a5a64 header, #d6acab accent, #f9f5f0 bg, footer with links + recipient)
  - [x] order_confirmation.html (items table, totals, promo discount, address/pickup info, CTA)
  - [x] order_cancelled.html (items summary, admin_notes if set, refund notice, shop-again CTA)
  - [x] order_packed.html (delivery vs pickup variant, items summary, address snapshot)
  - [x] welcome.html (feature bullets, shop CTA)
  - [x] password_reset.html (button + plaintext fallback link, 1-hour expiry notice)
- [x] Hook into order status update flow (orders.py PUT /status → BackgroundTasks)
- [x] Use FastAPI BackgroundTasks for non-blocking sends (orders + auth routes)
- [x] Error handling: logs error without raising, RESEND_API_KEY guard skips gracefully
- [x] **Verify**: all 5 templates render ✅ (7100–8000 chars each), imports clean ✅
- [ ] **Verify**: emails send correctly on order status changes

## ✅ Prompt 8: Frontend - Shared UI Components & Layout — DONE
- [x] UI Components:
  - [x] Button (primary/secondary/ghost, sm/md/lg, loading state, fullWidth, forwardRef)
  - [x] Input (text/email/tel/number/password + label/error/helper, forwardRef)
  - [x] Select, Textarea, Badge, Card, Modal, Skeleton, Toast, Spinner, Divider
  - [x] QuantitySelector (- / input / +, min/max, stock-aware)
- [x] Layout Components:
  - [x] Header (sticky, hamburger left, logo center with overlaid text, cart icon right with badge)
  - [x] MobileMenu (slide-in drawer, backdrop, Escape key, nav links, login/signup, close button)
  - [x] Footer ("Made with 100% Love", Instagram + WhatsApp SVG icons, copyright, policy links)
  - [x] PromoBanner (sessionStorage dismiss, "Free Shipping above 200AED · Use code FREESHIP")
- [x] Root layout: Google Fonts via next/font, Tailwind theme, Header + main + Footer, dark mode init script
- [x] API client (apps/web/lib/api.ts): typed fetch wrapper, auth headers, session-id header, error handling
- [x] Cart context (apps/web/lib/cart-context.tsx): state, add/remove/update/clear, optimistic updates, session persistence
- [x] **Verify**: `pnpm build` passes with no type errors ✅

## ✅ Prompt 9: Frontend - Homepage — DONE
- [x] Promo Banner (in layout, from Prompt 8)
- [x] Hero section: 2-col grid (tagline + baker photo with offset border), 3-col action shots below
- [x] Featured products: server-side fetch (5m revalidate), horizontal scroll mobile / 4-col grid desktop, product cards with Add to Cart
- [x] Meet the Baker: full-width bg image (person_shot_4), primary/75 overlay, bordered label, italic quote, READ MORE CTA
- [x] "We Cater To": 6-col occasion grid (Birthdays, Weddings, Corporate, Eid, Ramadan, Celebrations)
- [x] SEO: metadata export, JSON-LD (Organization + WebSite + LocalBusiness), semantic HTML (section/article/h1/h2)
- [x] All images via next/image with alt text, priority on hero, responsive sizes
- [x] **Verify**: `pnpm build` passes cleanly ✅, featured products gracefully handle API unavailability

## ✅ Prompt 10: Frontend - Product Listing Pages — DONE
- [x] Dynamic route: apps/web/app/[category]/page.tsx (Next.js 15 async params)
- [x] Category title (primary, Playfair, uppercase, tracking-widest) + optional description
- [x] Product grid: 1 col mobile, 2 tablet, 3 desktop (gap-6 sm:gap-8)
- [x] Product card: image, name, divider, price, variant selector (when >1 active variant), QuantitySelector, ADD TO CART
- [x] Add to cart: toast success/error, header badge auto-updates via CartContext, out-of-stock overlay
- [x] Server-side data fetching (parallel category + products fetch), notFound() on unknown slug, skeleton loading.tsx
- [x] SEO: generateMetadata (title + description + OG), JSON-LD BreadcrumbList + ItemList
- [x] Empty state: inventory_2 icon + message
- [x] **Verify**: `pnpm build` passes ✅, /[category] renders as dynamic server route

## ✅ Prompt 11: Frontend - Cart Page — DONE
- [x] "MY CART" heading with item count
- [x] Cart items: thumbnail, name+variant, unit price, quantity selector, line total, remove button
- [x] Order summary: subtotal, promo code input + apply, discount line, delivery (calculated at checkout note), total, PROCEED TO CHECKOUT
- [x] Empty cart state: shopping_bag icon + message + Continue Shopping
- [x] Optimistic updates with rollback via CartContext
- [x] Promo code validation via POST /promo-codes/validate, apply/remove flow
- [x] Guest checkout support: creates guest session via POST /auth/guest before redirecting to /checkout
- [x] **Verify**: `pnpm build` passes ✅, /cart renders as static page (116 kB)

## ✅ Prompt 12: Frontend - Checkout Flow — DONE
- [x] Step indicators (1 Information → 2 Delivery → 3 Payment) with completion state
- [x] Step 1 (Information): email, name, phone; address form (saved addresses for auth users); order summary sidebar
- [x] Step 2 (Delivery): radio cards (Delivery 35-50 AED / Pickup free), free delivery threshold note, "orders before 12PM" info
- [x] Step 3 (Payment): totals review, promo code input+apply, payment method (Stripe enabled / Tabby+Tamara coming soon), PAY NOW with total
- [x] Confirmation page: success icon, order number, items, totals, delivery info, Continue Shopping + View Orders CTAs
- [x] sessionStorage persistence across steps, step validation before advancing, back navigation
- [x] Stripe cancel URL resume: detects ?step=payment return, resumes at payment step
- [x] Guest session auto-created before order if not authenticated
- [x] New types: Address, AddressCreate, EmirateEnum, OrderCreate, PaymentSessionResponse
- [x] New API modules: addressesApi, ordersApi, paymentsApi
- [x] **Verify**: `pnpm build` passes ✅ — /checkout (119 kB), /checkout/confirmation (109 kB) both static

## ✅ Prompt 13: Frontend - User Account Pages — DONE
- [x] Login page: email/password, forgot password, signup link, guest checkout link
- [x] Signup page: full form, welcome email trigger
- [x] Forgot password + reset password pages
- [x] Profile page: welcome, quick links (orders/addresses/settings), logout
- [x] Orders page: list with color-coded status badges (created/confirmed/packed/cancelled)
- [x] Order detail: status timeline, items, totals, delivery/payment info
- [x] Addresses page: list, add new, edit/delete, set default
- [x] Settings page: edit profile, change password (via email reset), delete account
- [x] Auth guard: /account/* client-side redirect to /login if unauthenticated
- [x] AuthProvider + useAuth hook (lib/auth-context.tsx)
- [x] Header: account icon → /account or /login based on auth state
- [x] MobileMenu: auth-aware (My Account + Sign Out when logged in)
- [x] authApi.updateMe() added to api client
- [x] **Verify**: `pnpm build` passes ✅ — 15 routes, no type errors

## ✅ Prompt 14: Frontend - Static Pages — DONE
- [x] About Me: hero (person_shot_1.jpg, bg-primary/70 overlay), story sections with offset-border photos, values grid (4 cards), CTA banner
- [x] FAQ: 8-question accordion (client component), numbered with Playfair counters, WhatsApp + Contact CTAs, FAQPage JSON-LD
- [x] Contact: 4 info cards (WhatsApp/Email/Location/Hours), Google Maps embed, contact form (opens WhatsApp with pre-filled message), Instagram + WhatsApp social links
- [x] SEO: metadata export on all pages, JSON-LD (Person/LocalBusiness on About, FAQPage on FAQ, LocalBusiness on Contact)
- [x] **Verify**: `pnpm build` passes ✅ — 18 routes, no type errors

## ✅ Prompt 15: Admin Dashboard - Layout & Product Management — DONE
- [x] Admin layout: collapsible sidebar nav (w-52/w-14), mobile hamburger overlay, top bar (user + logout), admin auth guard
- [x] Admin login: email/password, verify is_admin flag
- [x] Dashboard overview: metrics cards (today's orders/revenue, total products, active promos), recent orders table, quick actions
- [x] Product list: table (image, name/slug, category, price, variants/stock, status badges, actions), search/filter, pagination
- [x] Product create/edit: form (name, auto-slug on create, category, description, price, featured, active), image upload (multi-file, preview with cover badge, move left/right, remove), variants section (add/remove inline, diff on edit)
- [x] Category management: CRUD with inline form, up/down reorder (swap display_order), toggle active, product count
- [x] **Verify**: admin app builds with 9 routes, no type errors ✅

## Prompt 16: Admin - Orders & Analytics
- [ ] Orders list: table (order#, customer, items, total, status badge, payment, date), filters, search, pagination, bulk actions, CSV export
- [ ] Order detail: full info, status timeline, customer/items/delivery/payment, status action buttons (Confirm/Pack/Cancel + email), admin notes
- [ ] Promo code management: list, create/edit, activate/deactivate
- [ ] Customer list: table (name, email, orders, total spent, joined), click to view details + orders
- [ ] Analytics page:
  - [ ] Date range selector
  - [ ] Revenue line chart, orders bar chart
  - [ ] Top products table (by revenue + quantity)
  - [ ] Sales by category pie chart
  - [ ] Shopping funnel with conversion rates
  - [ ] Average order value
- [ ] Backend analytics API: GET /analytics/overview, /revenue, /orders, /top-products, /funnel
- [ ] **Verify**: admin can manage orders, view analytics

## Prompt 17: Analytics, SEO & Performance
- [ ] Umami analytics: tracking script, custom events (page_view, product_view, add_to_cart, checkout_*, order_completed, promo_applied)
- [ ] Umami in docker-compose.yml
- [ ] sitemap.xml (auto-generated from categories + products)
- [ ] robots.txt (allow all, disallow /account/, /checkout/, /cart/)
- [ ] Structured data consolidation across all pages
- [ ] OG images (dynamic or brand images)
- [ ] Breadcrumb navigation component
- [ ] Image optimization: next/image everywhere, WebP, lazy load, priority LCP
- [ ] Font optimization: next/font, font-display swap
- [ ] Code splitting: dynamic imports for heavy components
- [ ] Prefetch category pages on hover
- [ ] Custom 404 and error pages (branded)
- [ ] **Verify**: Lighthouse SEO > 90, analytics events fire

## Prompt 18: Deployment & DevOps
- [ ] Dockerfiles: api (python:3.12-slim, multi-stage, uvicorn 4 workers), web (node:20-alpine, standalone), admin (same)
- [ ] docker-compose.yml (dev): postgres, fastapi hot-reload, web dev, admin dev, umami
- [ ] docker-compose.prod.yml: all services + nginx + certbot SSL
- [ ] Nginx config: reverse proxy (web/admin/api subdomains), SSL, gzip, caching, security headers, rate limiting
- [ ] CI/CD (.github/workflows/): deploy.yml (main push), pr-check.yml (lint/type/test)
- [ ] Environment: .env.example, .env.production.example, per-service env files
- [ ] Scripts: setup.sh, deploy.sh, backup-db.sh, restore-db.sh
- [ ] Health check endpoints for all services
- [ ] **Verify**: `docker compose up` runs full stack, nginx proxies work
