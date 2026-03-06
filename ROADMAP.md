# Melting Moments Ecommerce — Audit & Roadmap

> Comprehensive system audit performed 2026-03-05.
> Items organized by priority tier and domain. Check off items as they're resolved.

---

## P0 — Critical (Security & Data Integrity)

_Must fix before production launch._

### Security

- [x] **Weak default SECRET_KEY** — Startup guard added in `apps/api/app/main.py` — refuses to boot in production with the default key.
- [x] **Weak session ID generation** — `apps/web/lib/api.ts` now uses `crypto.randomUUID()`.
- [x] **CORS allows all methods and headers** — Restricted to `GET POST PUT PATCH DELETE` + `Content-Type Authorization X-Session-Id`.
- [x] **No rate limiting on auth endpoints** — `slowapi` added; login, register, forgot-password capped at 5/minute.
- [x] **JWT token has no revocation mechanism** — 30-min access tokens + refresh token rotation (`RefreshToken` model, `/auth/refresh`, `/auth/logout`).
- [x] **Admin routes lack role verification audit** — All admin routes confirmed using `get_admin_user` dependency (`apps/api/app/core/deps.py:85`). categories, products, orders, users, analytics, promo_codes, uploads all verified.
- [x] **Docker Compose secrets in plain text** — `docker-compose.yml` uses `${POSTGRES_USER:-mm_user}` etc. with `.env.example` template added.
- [x] **Stripe webhook secret not enforced at startup** — Startup guard in `main.py` raises `RuntimeError` if `STRIPE_WEBHOOK_SECRET` is empty in production.
- [x] **Missing security headers** — `X-Content-Type-Options: nosniff` always; `Strict-Transport-Security` in production; `TrustedHostMiddleware` added.
- [x] **No request payload size limit** — `MaxBodySizeMiddleware` rejects bodies >10 MB.

### Data Integrity

- [x] **Stock decrement race condition** — Atomic `UPDATE ... WHERE stock_quantity >= qty` with rowcount check; prevents overselling under concurrency.
- [x] **Order number generation race condition** — `SELECT ... FOR UPDATE` serializes concurrent generation on the same day prefix.
- [x] **Promo code usage increment is non-atomic** — Atomic `UPDATE ... WHERE current_uses < max_uses RETURNING id`; raises error if the row wasn't updated (limit reached concurrently).

---

## P1 — High (Bugs & Core Missing Features)

_Fix within 1-2 sprints._

### Bugs

- [x] **Admin dashboard order links go to list, not detail** — Fixed: `/orders/${order.order_number}` deep-link in place.
- [x] **Product detail page does not exist** — `apps/web/app/[category]/[product]/page.tsx` fully implemented.
- [x] **No search page** — `apps/web/app/search/page.tsx` exists with full filtering + pagination.
- [x] **Placeholder WhatsApp number** — Replaced with real number `+971563526578` across all pages.
- [x] **Category page silently swallows fetch errors** — Now logs `[category] Failed to load data for slug:` and returns `null` (404) instead of swallowing the error.
- [x] **Missing privacy and terms pages** — `apps/web/app/privacy/page.tsx` and `apps/web/app/terms/page.tsx` both exist.
- [x] **Hardcoded placeholder phone in contact & footer** — Replaced with real number `+971 56 352 6578`.
- [x] **Cart merge ignores stock limits** — Merged quantity is now capped at `product.stock_quantity` for stock-tracked products.
- [x] **Payment status overly permissive for BNPL** — Removed shortcut; Tabby/Tamara orders stay `paid=False` until webhook confirmation.
- [x] **Cart quantity has no bounds validation** — `Field(ge=1, le=99)` in place on `CartItemCreate` and `CartItemUpdate`.

### Missing Core Features

- [x] **No product detail page** — `apps/web/app/[category]/[product]/page.tsx` fully implemented.
- [x] **No image upload for products (admin)** — `apps/admin/components/products/ProductForm.tsx` + `apps/api/app/api/v1/uploads.py` fully implemented.
- [x] **No email delivery verification** — `apps/api/app/services/email_service.py` uses Resend with error logging and delivery status tracking.

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
- [ ] **Tabby & Tamara payment providers are stubs** — `apps/api/app/services/providers/tabby_provider.py` and `tamara_provider.py` raise errors. Either implement or remove from checkout UI to avoid customer confusion.
- [ ] **Shared UI package is empty** — `packages/ui/src/index.ts` exports nothing (just `export {}`). Both `apps/web` and `apps/admin` duplicate their own Button, Input, Modal, Badge, etc. Extract shared components.
---

_Last updated: 2026-03-05_
