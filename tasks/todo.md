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

## Prompt 2: Database Models & Migrations
- [ ] Base model class (id UUID PK, created_at, updated_at)
- [ ] User model (email, hashed_password nullable, first/last name, phone, is_active/admin/guest)
- [ ] Address model (user FK, label, full address fields, emirate enum, is_default)
- [ ] Category model (name, slug unique, description, image_url, display_order, is_active)
- [ ] Product model (category FK, name, slug unique, description, base_price, image_urls ARRAY, is_featured)
- [ ] ProductVariant model (product FK, name, sku unique, price, stock_quantity)
- [ ] Cart + CartItem models (user/session support for guests)
- [ ] Order model (order_number auto "MM-YYYYMMDD-XXX", status enum, delivery/payment fields, address snapshot JSONB)
- [ ] OrderItem model (snapshots of product/variant names at order time)
- [ ] PromoCode model (percentage/fixed, min_order, max_uses, valid dates)
- [ ] Database session setup (async engine + session factory) in core/database.py
- [ ] Alembic env.py configured for async
- [ ] Initial migration creating all tables
- [ ] Seed script (scripts/seed_db.py):
  - [ ] 5 categories (Brownies, Cookies, Cookie Melt, Mix Boxes, Desserts)
  - [ ] 5-10 products per category with realistic names/prices + variants (3/6/9 pcs, etc.)
  - [ ] 1 admin user (admin@meltingmomentscakes.com)
  - [ ] 2 promo codes: MM10 (10% off), FREESHIP (free shipping)
- [ ] Pydantic schemas for all models (Create, Update, Response)
- [ ] **Verify**: migrations run, seed populates data, can query DB

## Prompt 3: Backend Core - Auth, Config & Middleware
- [ ] Config (pydantic-settings): DATABASE_URL, SECRET_KEY, CORS origins, STRIPE/RESEND/R2 keys, APP_ENV
- [ ] Security: JWT creation/verification (python-jose), password hashing (passlib bcrypt)
- [ ] Dependencies: get_db, get_current_user, get_current_active_user, get_admin_user, get_optional_user
- [ ] Auth API:
  - [ ] POST /auth/register
  - [ ] POST /auth/login (return JWT)
  - [ ] POST /auth/guest (guest session JWT)
  - [ ] GET /auth/me, PUT /auth/me
  - [ ] POST /auth/forgot-password, POST /auth/reset-password
- [ ] Middleware: CORS, Request ID (X-Request-ID), Logging (method, path, status, duration)
- [ ] Main app: metadata, routers under /api/v1, middleware, startup/shutdown, /health endpoint
- [ ] Error handling: custom exceptions (NotFound, BadRequest, Unauthorized, Forbidden) + global handlers
- [ ] **Verify**: Swagger UI at localhost:8000/docs, auth flow works

## Prompt 4: Backend API - Products, Cart & Categories
- [ ] Categories API: GET /categories (with product count), GET /{slug}, POST/PUT/DELETE (admin)
- [ ] Products API:
  - [ ] GET /products (filters: category, search, featured, sort, pagination)
  - [ ] GET /products/{slug} (with variants)
  - [ ] GET /products/featured
  - [ ] POST/PUT/DELETE (admin)
  - [ ] Variant CRUD (admin)
- [ ] Cart API:
  - [ ] GET /cart (by user_id or session_id)
  - [ ] POST /cart/items, PUT /cart/items/{id}, DELETE /cart/items/{id}
  - [ ] DELETE /cart (clear)
  - [ ] POST /cart/merge (guest → user after login)
  - [ ] Calculate subtotal, item count, check stock
- [ ] Service layer: product_service, cart_service, category_service
- [ ] Image upload API: POST/DELETE /uploads/image (Cloudflare R2, validate type/size)
- [ ] **Verify**: all endpoints work via Swagger

## Prompt 5: Backend API - Orders, Delivery & Promo Codes
- [ ] Delivery service:
  - [ ] Dubai/Sharjah/Ajman → 35 AED, rest of UAE → 50 AED, pickup → free
  - [ ] Free shipping if subtotal >= 200 AED
  - [ ] GET /delivery/rates, POST /delivery/calculate
- [ ] Promo Code API:
  - [ ] POST /promo-codes/validate (check active, dates, max_uses, min_order)
  - [ ] Admin CRUD
- [ ] Orders API:
  - [ ] POST /orders (validate stock → calc totals → apply promo → calc delivery → create → clear cart)
  - [ ] Order number format: "MM-YYYYMMDD-XXX"
  - [ ] GET /orders (user's orders, paginated)
  - [ ] GET /orders/{order_number}
  - [ ] PUT /orders/{order_number}/status (admin, validated transitions + email triggers)
  - [ ] GET /orders/admin/all (admin, filters)
- [ ] Order service: create_order, update_status, calculate totals (5% VAT included)
- [ ] Address API: GET, POST, PUT, DELETE, PUT /{id}/default
- [ ] **Verify**: can create full order from cart

## Prompt 6: Backend API - Payments (Stripe + Tabby + Tamara)
- [ ] Payment service with abstract interface: create_session, verify_payment, handle_webhook
- [ ] StripePaymentProvider:
  - [ ] Stripe Checkout Session (AED, line items, metadata, success/cancel URLs)
  - [ ] Cards + Apple Pay support
- [ ] TabbyPaymentProvider (stub with TODO)
- [ ] TamaraPaymentProvider (stub with TODO)
- [ ] Payments API:
  - [ ] POST /payments/create-session (order_id, provider)
  - [ ] POST /payments/webhooks/stripe (verify signature, handle completed/failed)
  - [ ] POST /payments/webhooks/tabby (stub)
  - [ ] POST /payments/webhooks/tamara (stub)
  - [ ] GET /payments/{order_id}/status
- [ ] Security: webhook signature verification, idempotency keys, audit logging
- [ ] **Verify**: Stripe test mode checkout works end-to-end

## Prompt 7: Backend API - Email Service
- [ ] Email service (Resend): send_email base, send_order_confirmation/cancelled/packed, send_welcome, send_password_reset
- [ ] Jinja2 email templates (mobile-responsive, branded):
  - [ ] Base template (logo, colors #8a5a64/#d6acab, footer)
  - [ ] order_confirmation.html, order_cancelled.html, order_packed.html
  - [ ] welcome.html, password_reset.html
- [ ] Hook into order status update flow
- [ ] Use FastAPI BackgroundTasks for non-blocking sends
- [ ] Error handling + logging for failed sends
- [ ] **Verify**: emails send correctly on order status changes

## Prompt 8: Frontend - Shared UI Components & Layout
- [ ] UI Components:
  - [ ] Button (primary/secondary/ghost, sm/md/lg)
  - [ ] Input (text/email/tel/number/password + label/error/helper)
  - [ ] Select, Textarea, Badge, Card, Modal, Skeleton, Toast, Spinner, Divider
  - [ ] QuantitySelector (- / input / +)
- [ ] Layout Components:
  - [ ] Header (sticky, hamburger left, logo center with overlaid text, cart icon right with badge)
  - [ ] MobileMenu (slide-in sidebar, nav links, login/signup, close button)
  - [ ] Footer ("Made with 100% Love", social links, copyright, policy links)
  - [ ] PromoBanner (dismissible, "Free Shipping above 200AED" + promo code)
- [ ] Root layout: Google Fonts via next/font, Tailwind theme, Header + main + Footer, dark mode provider
- [ ] API client (apps/web/lib/api.ts): typed fetch wrapper, auth headers, error handling
- [ ] Cart context (apps/web/lib/cart-context.tsx): state, add/remove/update/clear, session persistence
- [ ] **Verify**: layout renders, responsive on mobile/desktop

## Prompt 9: Frontend - Homepage
- [ ] Promo Banner (in layout)
- [ ] Hero section: 2-col grid (text block + baker photo), 3-col action shots below
- [ ] Featured products: "OUR BESTSELLERS", horizontal scroll mobile / grid desktop, product cards with ADD TO CART
- [ ] Meet the Baker: full-width bg image, primary overlay, bordered label, description, READ MORE link
- [ ] Categories "WE CATER TO": grid cards (Birthdays, Weddings, Corporate, Eid, Ramadan, Celebrations)
- [ ] SEO: metadata, JSON-LD (Organization, WebSite, LocalBusiness), semantic HTML
- [ ] All images via next/image with alt text, lazy loading, responsive sizes
- [ ] **Verify**: homepage matches design reference, responsive

## Prompt 10: Frontend - Product Listing Pages
- [ ] Dynamic route: apps/web/app/[category]/page.tsx
- [ ] Category title (primary, Playfair, uppercase, tracking-wide)
- [ ] Product grid: 1 col mobile, 2 tablet, 3 desktop
- [ ] Product card: image, name, divider, price, variant selector (dropdown), quantity selector, ADD TO CART
- [ ] Add to cart: toast notification, update header badge, handle existing items
- [ ] Server-side data fetching, 404 handling, skeleton loading
- [ ] SEO: dynamic metadata, JSON-LD BreadcrumbList + ItemList
- [ ] Empty state message
- [ ] **Verify**: category pages render, add to cart works

## Prompt 11: Frontend - Cart Page
- [ ] "MY CART" heading with item count
- [ ] Cart items: thumbnail, name+variant, unit price, quantity selector, line total, remove button
- [ ] Order summary: subtotal, promo code input + apply, discount line, delivery estimate, total, PROCEED TO CHECKOUT
- [ ] Empty cart state: icon + message + Continue Shopping
- [ ] Optimistic updates with rollback, promo validation via API
- [ ] Guest checkout support (create guest session if needed)
- [ ] **Verify**: cart CRUD works, promo codes validate

## Prompt 12: Frontend - Checkout Flow
- [ ] Checkout sub-layout: simplified header, step indicators
- [ ] Step 1 (Information): email (guest) or logged-in state, address form (saved or new), order summary sidebar
- [ ] Step 2 (Delivery): radio cards (Delivery w/ fee or Pickup free), delivery time note
- [ ] Step 3 (Payment): order summary, promo code, payment method selection (Stripe/Tabby/Tamara), PAY NOW
- [ ] Confirmation page: success animation, order number, summary, email notice, Continue Shopping / View Order
- [ ] State persistence across steps (URL params or context), step validation, back navigation
- [ ] **Verify**: full checkout flow works end-to-end with Stripe test mode

## Prompt 13: Frontend - User Account Pages
- [ ] Login page: email/password, forgot password, signup link, guest checkout link
- [ ] Signup page: full form, welcome email trigger
- [ ] Profile page: welcome, quick links (orders/addresses/settings), logout
- [ ] Orders page: list with status badges (colored by state), expand for details
- [ ] Order detail: status timeline, items with images, delivery/payment info, totals
- [ ] Addresses page: list, add new, edit/delete, set default
- [ ] Settings page: edit profile, change password, delete account
- [ ] Auth middleware: /account/* requires auth, redirect to /login
- [ ] **Verify**: full auth flow + account management works

## Prompt 14: Frontend - Static Pages
- [ ] About Me: hero image + overlay, story section with photos, values, CTA
- [ ] FAQ: accordion Q&A (8 questions), CTA at bottom
- [ ] Contact: info cards (WhatsApp, email, location), Google Maps embed, contact form, social links
- [ ] SEO: metadata, JSON-LD (FAQPage, LocalBusiness)
- [ ] **Verify**: pages render with proper content

## Prompt 15: Admin Dashboard - Layout & Product Management
- [ ] Admin layout: collapsible sidebar nav, top bar (user + logout), main content, admin auth required
- [ ] Admin login: email/password, verify is_admin
- [ ] Dashboard overview: metrics cards (today's orders/revenue, total products, active promos), recent orders, quick actions
- [ ] Product list: table (image, name, category, price, stock, status, actions), search/filter, pagination
- [ ] Product create/edit: form (name, auto-slug, category, description, price, featured, active), image upload (drag-drop, preview, reorder), variants section
- [ ] Category management: CRUD, drag-to-reorder
- [ ] **Verify**: admin can manage products and categories

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
