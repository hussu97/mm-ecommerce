# Melting Moments Ecommerce — Audit & Roadmap

> Comprehensive system audit performed 2026-03-05.
> Items organized by priority tier and domain. Check off items as they're resolved.

---

## P0 — Critical (Security & Data Integrity)

_Must fix before production launch._

### Security

- [ ] **Weak default SECRET_KEY** — `apps/api/app/core/config.py:20` uses `"change-me-in-production-use-a-long-random-string-here"`. No runtime guard rejects this in production. Add a startup check that refuses to boot with the default key when `APP_ENV=production`.
- [ ] **Weak session ID generation** — `apps/web/lib/api.ts:37` uses `Math.random().toString(36)` which is not cryptographically random. Replace with `crypto.randomUUID()` or `crypto.getRandomValues()`.
- [ ] **CORS allows all methods and headers** — `apps/api/app/main.py:47-48` sets `allow_methods=["*"]` and `allow_headers=["*"]`. Restrict to the specific methods and headers actually used (GET, POST, PUT, PATCH, DELETE + Content-Type, Authorization, X-Session-Id).
- [ ] **No rate limiting on auth endpoints** — `apps/api/app/api/v1/auth.py` login, register, forgot-password have no rate limiting. Add `slowapi` or equivalent to prevent brute-force and credential-stuffing attacks.
- [ ] **JWT token has no revocation mechanism** — 7-day expiry (`ACCESS_TOKEN_EXPIRE_MINUTES: 10080` at `config.py:21`) with no token blocklist. If a token is compromised, it stays valid for a week. Add token revocation (Redis blocklist or short-lived tokens + refresh token rotation).
- [x] **Admin routes lack role verification audit** — All admin routes confirmed using `get_admin_user` dependency (`apps/api/app/core/deps.py:85`). categories, products, orders, users, analytics, promo_codes, uploads all verified.
- [ ] **Docker Compose secrets in plain text** — `docker-compose.yml:8-9` has `mm_user` / `mm_password` and `docker-compose.yml:42` has Umami `APP_SECRET: change-me-in-production-to-a-random-secret`. Use Docker secrets or `.env` file excluded from VCS.
- [ ] **Stripe webhook secret not enforced at startup** — `apps/api/app/services/providers/stripe_provider.py:113-122` only checks `STRIPE_WEBHOOK_SECRET` when a webhook arrives. In production, fail fast at startup if this is empty.
- [ ] **Missing security headers** — API responses lack `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, and `TrustedHostMiddleware`. Add these for production.
- [ ] **No request payload size limit** — No middleware caps request body size. Oversized payloads could cause OOM. Add a max body size middleware (e.g., 10 MB).

### Data Integrity

- [ ] **Stock decrement race condition** — `apps/api/app/services/order_service.py:163` does `variant.stock_quantity -= quantity` as a Python-side decrement without `SELECT ... FOR UPDATE` or an atomic SQL `UPDATE ... SET stock = stock - :qty WHERE stock >= :qty`. Concurrent orders can oversell. Fix with pessimistic locking or optimistic concurrency (version column).
- [ ] **Order number generation race condition** — `apps/api/app/services/order_service.py:33-51` reads the latest order number and increments in Python. Two concurrent requests on the same day can generate duplicate order numbers. Use a DB sequence or `SELECT ... FOR UPDATE`.
- [ ] **Promo code usage increment is non-atomic** — `apps/api/app/services/order_service.py:167` does `promo_obj.current_uses += 1` in Python. Concurrent redemptions can bypass the `max_uses` limit. Use atomic SQL increment with a WHERE guard.

---

## P1 — High (Bugs & Core Missing Features)

_Fix within 1-2 sprints._

### Bugs

- [ ] **Admin dashboard order links go to list, not detail** — `apps/admin/app/(dashboard)/page.tsx:132` links to `/orders` instead of `/orders/${order.order_number}`. Each order row should deep-link to its detail page.
- [ ] **Product detail page does not exist** — `apps/web/app/[category]/[product]/` directory is missing entirely. ProductCard links lead to 404. This is the most critical missing storefront page — users can browse categories but cannot view or purchase any product.
- [ ] **No search page** — `apps/web/app/search/` does not exist. The API supports `?search=` but there's no frontend search experience.
- [ ] **Placeholder WhatsApp number** — `apps/web/app/account/settings/page.tsx:171` has `href="https://wa.me/971XXXXXXXXX"`. Replace with real number before launch.
- [ ] **Tabby & Tamara payment providers are stubs** — `apps/api/app/services/providers/tabby_provider.py` and `tamara_provider.py` raise errors. Either implement or remove from checkout UI to avoid customer confusion.
- [ ] **Category page silently swallows fetch errors** — `apps/web/app/[category]/page.tsx:25-26` catches all errors and returns `null`, making debugging impossible and potentially showing 404 for transient API failures.
- [ ] **Missing privacy and terms pages** — `apps/web/components/layout/Footer.tsx:71` and `apps/web/app/signup/page.tsx:135,137` link to `/privacy` and `/terms` which don't exist. Legal/compliance risk for production.
- [ ] **Hardcoded placeholder phone in contact & footer** — `apps/web/app/contact/page.tsx:18` and `apps/web/components/layout/Footer.tsx:45` show a placeholder phone number. Replace with real business number.
- [ ] **Cart merge ignores stock limits** — `apps/api/app/services/cart_service.py:264` merges guest cart into user cart by summing quantities without validating combined quantity doesn't exceed stock.
- [ ] **Payment status overly permissive for BNPL** — `apps/api/app/services/payment_service.py:127-130` marks orders as paid for Tabby/Tamara if any `payment_id` exists, without webhook confirmation. Will cause unpaid orders to ship once providers are integrated.
- [ ] **Cart quantity has no bounds validation** — `apps/api/app/schemas/cart.py` allows any integer for quantity, including negative or extremely large values. Add `Field(ge=1, le=99)`.

### Missing Core Features

- [ ] **No product detail page** — Implement `apps/web/app/[category]/[product]/page.tsx` with product images, variant selection, add-to-cart, description, and related products.
- [ ] **Shared UI package is empty** — `packages/ui/src/index.ts` exports nothing (just `export {}`). Both `apps/web` and `apps/admin` duplicate their own Button, Input, Modal, Badge, etc. Extract shared components.
- [ ] **No image upload for products (admin)** — Admin product forms need image upload connected to Cloudflare R2.
- [ ] **No email delivery verification** — Email service (`apps/api/app/services/email_service.py`) uses Resend but there's no verification that emails actually send in production. Add logging, error handling, and delivery status tracking.

---

## P2 — Medium (UX, Performance, Code Quality)

_Next quarter._

### Deprecations & Code Quality

- [ ] **Deprecated `@app.on_event` usage** — `apps/api/app/main.py:100-107` uses `@app.on_event("startup")` and `@app.on_event("shutdown")` which are deprecated in FastAPI. Replace with `lifespan` context manager.
- [ ] **No `__all__` exports in Python modules** — API service modules lack explicit `__all__` definitions, making the public interface ambiguous.
- [ ] **`search` parameter in `ilike` not sanitized for wildcards** — `apps/api/app/services/product_service.py:64`, `order_service.py:284`, `users.py:74-76` pass user input directly into `ilike(f"%{search}%")`. While SQLAlchemy parameterizes values (no SQL injection), special characters like `%` and `_` in user input match as wildcards. Escape them.
- [ ] **Token stored in localStorage** — `apps/web/lib/api.ts:21` stores JWT in `localStorage` which is vulnerable to XSS. Consider `httpOnly` cookies for sensitive tokens.
- [ ] **API error class inconsistency** — Auth routes (`apps/api/app/api/v1/auth.py`) use raw `HTTPException` while other routes use custom `AppError` subclasses. Standardize on one pattern.
- [ ] **Guest user cleanup** — `apps/api/app/api/v1/auth.py:125-145` creates unlimited guest users that accumulate forever. Add a scheduled cleanup for stale guest accounts.
- [ ] **No error tracking integration** — `apps/web/app/error.tsx:15` only logs to `console.error`. No Sentry or equivalent for production error monitoring.
- [ ] **Health check doesn't verify DB** — `apps/api/app/main.py:90-92` returns `{"status": "ok"}` without checking database connectivity. Add a `SELECT 1` probe.
- [ ] **No audit logging for admin actions** — No trail of who updated orders, deleted products, or modified promo codes. Add an audit log table.
- [ ] **Promo code discount value has no positive constraint** — `apps/api/app/models/promo_code.py:25` allows zero or negative `discount_value` at the DB level. Add a CHECK constraint.
- [ ] **No unique constraint on cart per user/session** — `apps/api/alembic/versions/001_initial_tables.py` allows multiple carts for the same user or session. Add a unique constraint.
- [ ] **Payment method field not enum-validated** — `apps/api/app/schemas/order.py:31` accepts arbitrary strings for `payment_method` instead of a `PaymentMethodEnum`. Users can pass invalid providers.

### UI/UX Design Issues

- [ ] **No loading states on storefront page transitions** — Navigation between pages has no loading indicator; feels unresponsive on slow connections.
- [ ] **Cart page missing empty state illustration** — When cart is empty, show an engaging illustration with a CTA to continue shopping, not just text.
- [ ] **Checkout flow missing order summary sidebar** — Desktop checkout should show a persistent order summary on the side.
- [ ] **Mobile category nav overflow** — If there are many categories, the horizontal category nav bar may overflow without scroll indicators.
- [ ] **No product image zoom** — Product images (once detail page exists) should support pinch-to-zoom on mobile and hover-zoom on desktop.
- [ ] **No breadcrumb on all pages** — Only category pages have breadcrumbs. Add to product detail, cart, checkout, and account pages for consistent navigation.
- [ ] **Admin dashboard mobile responsiveness** — Verify admin app works on tablet/mobile for on-the-go order management.
- [ ] **FeaturedProducts horizontal scroll has no indicators** — `apps/web/components/home/FeaturedProducts.tsx:94` uses `snap-x` scroll but no visual cues that more products exist off-screen.
- [ ] **Checkout phone validation too loose** — `apps/web/app/checkout/page.tsx:213` only checks `length > 7`. Accepts clearly invalid inputs like `+971 1`.

### Performance

- [ ] **No image optimization pipeline** — Product images served directly from R2 without CDN resizing. Implement responsive `srcset` with on-the-fly resizing (Cloudflare Image Resizing or `next/image` with a loader).
- [ ] **No database connection pooling configuration** — `apps/api/app/core/config.py` has no pool size tuning. Default asyncpg pool may be insufficient under load. Configure `pool_size`, `max_overflow`, `pool_recycle`.
- [ ] **Category page fetches up to 50 products without pagination** — `apps/web/app/[category]/page.tsx:15` requests `per_page=50` with no client-side pagination or infinite scroll.
- [ ] **No caching layer** — No Redis or in-memory cache for frequently accessed data (categories, featured products). Every request hits the DB.

---

## P3 — Low (Nice-to-have Features & Polish)

_Backlog — prioritize as bandwidth allows._

### Testing & CI/CD

- [ ] **Zero test files in the entire project** — No unit tests, integration tests, or E2E tests exist anywhere in `apps/` or `packages/`. Start with:
  - API: pytest + httpx for endpoint tests (auth, cart, orders, products)
  - Web: Vitest + React Testing Library for component tests
  - E2E: Playwright for critical user flows (browse → add to cart → checkout)
- [ ] **CI runs no tests** — `pr-check.yml` runs lint + type-check + build but no tests. Add test steps once test suites exist.
- [ ] **Deploy workflow swallows migration failures** — `.github/workflows/deploy.yml:36` uses `|| true` after `alembic upgrade head`. A failed migration silently proceeds, potentially leaving the DB in an inconsistent state. Remove `|| true` and fail the deployment.
- [ ] **No staging environment** — Only production deployment exists. Add a staging environment for pre-production validation.
- [ ] **No health check for frontend containers** — `docker-compose.yml` web and admin services have no health checks, unlike postgres.
- [ ] **No rollback strategy** — Deploy workflow has no rollback mechanism. A bad deploy requires manual SSH intervention.

### Feature Requests (Customer Experience)

- [ ] **Product reviews & ratings** — Allow customers to leave reviews with star ratings. Display average rating on product cards.
- [ ] **Order tracking with status updates** — Email notifications when order status changes (confirmed, packed, shipped). Real-time tracking page.
- [ ] **Multi-language support (Arabic)** — UAE market expects Arabic. Implement i18n with RTL layout support.
- [ ] **WhatsApp order notifications** — Send order confirmation and status updates via WhatsApp Business API (very popular in UAE).
- [ ] **Gift wrapping & gift messages** — Add option during checkout for gift packaging with a custom message.
- [ ] **Loyalty / rewards program** — Points-based system for repeat customers (e.g., 1 AED = 1 point, 100 points = 10 AED discount).
- [ ] **Scheduled delivery / pre-orders** — Let customers pick a delivery date, especially for event cakes.
- [ ] **Social login (Google, Apple)** — Reduce registration friction with OAuth providers.
- [ ] **Recently viewed products** — Show recently browsed products for easy re-discovery.

### Feature Requests (Platform & Operations)

- [ ] **Inventory alerts** — Admin notification when stock runs low (configurable threshold per variant).
- [ ] **Sales reports export** — CSV/Excel export for orders, revenue, and product performance from admin dashboard.
- [ ] **Automated backup strategy** — Scheduled PostgreSQL backups to cloud storage (GCS/S3) with point-in-time recovery.
- [ ] **API versioning strategy** — Current `/api/v1` has no plan for v2 migration. Document the versioning approach.
- [ ] **Structured logging** — Replace basic `logging.basicConfig` with structured JSON logging for production (better for log aggregation in GCP).
- [ ] **Database indexes audit** — Review query patterns and add missing indexes (e.g., `orders.email`, `orders.created_at`, `products.slug`, `users.email` if not already indexed).
- [ ] **Content management** — Static pages (About, FAQ, Contact) are hardcoded in Next.js. Consider a lightweight CMS or admin-editable content.
- [ ] **Webhook retry mechanism** — Payment webhooks (Stripe) should have retry logic and idempotency keys to handle transient failures.
- [ ] **API request/response logging** — The logging middleware exists but verify it captures enough context for debugging without logging sensitive data (passwords, tokens).
- [ ] **Container image size optimization** — Review Dockerfiles for multi-stage build efficiency. Minimize final image size for faster deploys.
- [ ] **Missing DB indexes for common queries** — `orders.created_at` (analytics), `product_variants.stock_quantity` (cart validation), `users.is_guest` + `users.is_admin` (customer list filters) lack indexes. Add them for performance at scale.

---

---

## Good to Have

- [ ] **Docs endpoints exposed in production** — `apps/api/app/main.py` exposes `/docs`, `/redoc`, and `/openapi.json` unconditionally. Disable in production or gate behind admin auth.

---

_Last updated: 2026-03-05_
