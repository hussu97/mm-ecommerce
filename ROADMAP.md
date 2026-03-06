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

- [x] **Deprecated `@app.on_event` usage** — Replaced with `@asynccontextmanager lifespan` pattern in `apps/api/app/main.py`.
- [ ] **No `__all__` exports in Python modules** — API service modules lack explicit `__all__` definitions, making the public interface ambiguous.
- [x] **`search` parameter in `ilike` not sanitized for wildcards** — `_escape_like()` helper added to product/order services and users API; escapes `%`, `_`, `\` before interpolation.
- [ ] **Token stored in localStorage** — `apps/web/lib/api.ts:21` stores JWT in `localStorage` which is vulnerable to XSS. Consider `httpOnly` cookies for sensitive tokens.
- [x] **API error class inconsistency** — `auth.py` now uses `ConflictError`, `UnauthorizedError`, `ForbiddenError`, `BadRequestError` throughout. All raw `HTTPException` removed.
- [x] **Guest user cleanup** — `DELETE /auth/guests/cleanup` (admin only) removes guests older than 30 days with no cart.
- [x] **No error tracking integration** — Sentry integrated via `instrumentation.ts` and `error.tsx`; gated on `NEXT_PUBLIC_SENTRY_DSN`, falls back to `console.error`.
- [x] **Health check doesn't verify DB** — `/health` now runs `SELECT 1` and returns 503 if DB is unreachable.
- [ ] **No audit logging for admin actions** — No trail of who updated orders, deleted products, or modified promo codes. Add an audit log table.
- [x] **Promo code discount value has no positive constraint** — `CheckConstraint("discount_value > 0")` added to model and applied via migration 005.
- [x] **No unique constraint on cart per user/session** — Partial unique index `ON carts (user_id) WHERE user_id IS NOT NULL` applied via migration 006.
- [x] **Payment method field not enum-validated** — `PaymentMethodEnum(stripe|tabby|tamara)` on `OrderCreate`; invalid values return 422.

### UI/UX Design Issues

- [x] **No loading states on storefront page transitions** — `loading.tsx` skeletons added for cart, checkout, and product detail pages.
- [x] **Cart page missing empty state illustration** — Already implemented (icon + CTA).
- [x] **Checkout flow missing order summary sidebar** — Already implemented (`OrderSummarySidebar` component).
- [x] **Mobile category nav overflow** — Right-side fade gradient overlay added when nav content overflows.
- [x] **No product image zoom** — `group-hover:scale-110` zoom on main image; thumbnails are now clickable with active ring indicator.
- [x] **No breadcrumb on all pages** — Added to cart (`Home > Cart`) and checkout (`Home > Cart > Checkout`); product detail already had it.
- [x] **Admin dashboard mobile responsiveness** — Already fully implemented (hamburger nav, collapsible sidebar).
- [x] **FeaturedProducts horizontal scroll has no indicators** — Left/right chevron arrow buttons added on mobile; show/hide based on scroll position.
- [x] **Checkout phone validation too loose** — Replaced `length < 7` with `/^\+?[0-9\s()\-+]{7,15}$/` regex, accepting UAE formats.

### Performance

- [x] **No image optimization pipeline** — `next/image` configured with explicit `deviceSizes`, `imageSizes`, `formats: ["image/webp"]`, and R2 remote patterns in `apps/web/next.config.ts` and `apps/admin/next.config.ts`.
- [x] **No database connection pooling configuration** — Already at 10/20 connections with `pool_pre_ping` (done in earlier work).
- [x] **Category page fetches up to 50 products without pagination** — Reduced to 12 per page; prev/next pagination via `?page=N` searchParam.
- [x] **No caching layer** — Redis cache added (`cache.py`, fail-open); 5 min TTL on `GET /categories` and `GET /products/featured`; invalidated on mutations. Redis service added to docker-compose.

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
- [x] **Multi-language support (Arabic)** — Full `[locale]` App Router, SSR `lang`/`dir`, RTL layout, Arabic content, admin Languages + Translations pages, i18n API (`apps/api/app/api/v1/i18n.py`).
- [ ] **WhatsApp order notifications** — Send order confirmation and status updates via WhatsApp Business API (very popular in UAE).
- [ ] **Gift wrapping & gift messages** — Add option during checkout for gift packaging with a custom message.
- [ ] **Loyalty / rewards program** — Points-based system for repeat customers (e.g., 1 AED = 1 point, 100 points = 10 AED discount).
- [ ] **Scheduled delivery / pre-orders** — Let customers pick a delivery date, especially for event cakes.
- [ ] **Social login (Google, Apple)** — Reduce registration friction with OAuth providers.
- [ ] **Recently viewed products** — Show recently browsed products for easy re-discovery.

### Feature Requests (Platform & Operations)

- [ ] **Sales reports export** — CSV/Excel export for orders, revenue, and product performance from admin dashboard.
- [ ] **Automated backup strategy** — Scheduled PostgreSQL backups to cloud storage (GCS/S3) with point-in-time recovery.
- [ ] **API versioning strategy** — Current `/api/v1` has no plan for v2 migration. Document the versioning approach.
- [ ] **Structured logging** — Replace basic `logging.basicConfig` with structured JSON logging for production (better for log aggregation in GCP).
- [ ] **Database indexes audit** — Review query patterns and add missing indexes (e.g., `orders.email`, `orders.created_at`, `products.slug`, `users.email` if not already indexed).
- [x] **Content management** — CMS model + API (`apps/api/app/api/v1/cms.py`), migrations 008–010, admin `/content` page; About, FAQ, Contact, Privacy all CMS-driven with EN + AR content.
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
- [ ] **Inventory alerts** — Admin notification when stock runs low (configurable threshold per variant).
---

_Last updated: 2026-03-06_
