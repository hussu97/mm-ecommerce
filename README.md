# Melting Moments Ecommerce

Full-stack ecommerce platform for **Melting Moments Cakes** — a UAE artisanal bakery specialising in handcrafted brownies, cookies and desserts. Built as a Turborepo monorepo replacing the previous Wix storefront.

---

## Overview

| App | Description | Port |
|-----|-------------|------|
| `apps/web` | Customer-facing Next.js storefront | 3000 |
| `apps/admin` | Internal order & product management dashboard | 3001 |
| `apps/api` | FastAPI backend — REST API + business logic | 8000 |
| Umami | Self-hosted analytics (Docker) | 3002 |

---

## Architecture

```
mm-ecommerce/
├── apps/
│   ├── web/          Next.js 15 storefront (App Router, TypeScript)
│   ├── admin/        Next.js 15 admin panel (App Router, TypeScript)
│   └── api/          FastAPI + SQLAlchemy 2.0 async backend (Python 3.12)
├── packages/
│   ├── ui/           @mm/ui  — shared React component library
│   ├── types/        @mm/types — shared TypeScript type definitions
│   └── config/       @mm/config — shared ESLint + TypeScript configs
├── docker-compose.yml        Local dev (Postgres, Umami, API, Web, Admin)
├── docker-compose.prod.yml   Production overrides
├── turbo.json
└── pnpm-workspace.yaml
```

```
Browser
  │
  ├── storefront (Next.js)  ──┐
  │                           ├── REST API (FastAPI /api/v1/*)
  └── admin (Next.js)       ──┘
                                   │
                              PostgreSQL 16
```

The two Next.js apps are thin presentation layers — all state and business logic lives in the FastAPI backend. Both apps fetch from the API at build time (RSC) and at runtime (client components).

---

## Tech Stack

### Frontend (web + admin)
- **Next.js 15** with App Router and React Server Components
- **TypeScript**
- **Tailwind CSS v4** — CSS-first config (`@theme {}` in `globals.css`)
- **pnpm** workspaces

### Backend
- **FastAPI** 0.115+
- **SQLAlchemy 2.0** async ORM + **asyncpg** driver
- **Alembic** for database migrations
- **Pydantic v2** for schema validation
- **python-jose** for JWT auth (access + refresh tokens)
- **slowapi** for rate limiting

### Infrastructure & Services
| Service | Purpose |
|---------|---------|
| PostgreSQL 16 | Primary database |
| Stripe | Payment processing |
| Resend | Transactional email |
| Cloudflare R2 | Product image storage |
| Umami | Self-hosted web analytics |

---

## Prerequisites

- **Node.js** ≥ 20
- **pnpm** ≥ 9.15 — `npm install -g pnpm`
- **Python** ≥ 3.12
- **Docker** + Docker Compose (for local Postgres and Umami)

---

## Local Development

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd mm-ecommerce
pnpm install
```

### 2. Start the database

```bash
docker compose up -d postgres
```

### 3. Set up the API

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env           # edit as needed
alembic upgrade head           # run migrations
python -m scripts.seed_db      # optional: seed sample data
```

### 4. Start all apps

The API must be running before the web/admin apps — they fetch data from it at startup.

**Option A — start everything together (recommended):**
```bash
pnpm dev
```

**Option B — start in separate terminals:**
```bash
# Terminal 1 — FastAPI backend (must start first)
pnpm dev:api

# Terminal 2 — Next.js storefront
pnpm dev:web

# Terminal 3 — Next.js admin
pnpm dev:admin
```

> If you see `Failed to load category data` or similar errors in the web app, the API is not running. Start it first.

### 5. Visit

| URL | Service |
|-----|---------|
| http://localhost:3000 | Storefront |
| http://localhost:3001 | Admin |
| http://localhost:8000/docs | API interactive docs (Swagger) |
| http://localhost:8000/redoc | API docs (ReDoc) |
| http://localhost:3002 | Umami analytics |

---

## Environment Variables

### API (`apps/api/.env`)

```env
# App
APP_ENV=development

# Database
DATABASE_URL=postgresql+asyncpg://mm_user:mm_password@localhost:5432/mm_ecommerce

# Security — change in production
SECRET_KEY=change-me-in-production-use-a-long-random-string-here
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=30

# CORS
CORS_ORIGINS=["http://localhost:3000","http://localhost:3001"]
ALLOWED_HOSTS=["*"]

# Stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Resend (email)
RESEND_API_KEY=re_...
FROM_EMAIL=orders@meltingmomentscakes.com

# Cloudflare R2 (image uploads)
CLOUDFLARE_R2_ACCESS_KEY=
CLOUDFLARE_R2_SECRET_KEY=
CLOUDFLARE_R2_BUCKET=mm-ecommerce
CLOUDFLARE_R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
CLOUDFLARE_R2_PUBLIC_URL=https://your-public-r2-url.com

# Frontend URLs
WEB_URL=http://localhost:3000
ADMIN_URL=http://localhost:3001
```

### Web storefront (`apps/web/.env.local`)

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_test_...
NEXT_PUBLIC_UMAMI_WEBSITE_ID=
NEXT_PUBLIC_UMAMI_URL=http://localhost:3002
```

### Admin (`apps/admin/.env.local`)

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

---

## Available Scripts

Run from the **repo root** unless noted.

| Script | Description |
|--------|-------------|
| `pnpm dev` | Start all apps in parallel via Turbo |
| `pnpm dev:web` | Storefront only |
| `pnpm dev:admin` | Admin only |
| `pnpm dev:api` | FastAPI with hot reload |
| `pnpm build` | Production build for all apps |
| `pnpm lint` | Lint all apps |
| `pnpm db:up` | Start Postgres container |
| `pnpm db:migrate` | Run Alembic migrations (`alembic upgrade head`) |
| `pnpm db:seed` | Seed sample categories and products |

---

## API Reference

Base URL: `http://localhost:8000/api/v1`

| Prefix | Resource |
|--------|----------|
| `/auth` | Register, login, refresh tokens, password reset |
| `/users` | User profile management |
| `/categories` | Product categories |
| `/products` | Product listing, detail, search |
| `/cart` | Guest + authenticated cart, merge on login |
| `/orders` | Order creation, status, history |
| `/addresses` | Saved delivery addresses |
| `/payments` | Stripe payment session, webhook handler |
| `/promo-codes` | Promo code validation |
| `/delivery` | Delivery time slots and fee lookup |
| `/uploads` | Image upload to Cloudflare R2 |
| `/analytics` | Internal analytics events |

Full interactive documentation is available at `/docs` when the API is running.

---

## Database

The schema is managed with **Alembic**. Migrations live in `apps/api/alembic/versions/`.

```bash
# Apply all pending migrations
cd apps/api && alembic upgrade head

# Create a new migration (after changing a model)
cd apps/api && alembic revision --autogenerate -m "describe change"

# Roll back one migration
cd apps/api && alembic downgrade -1
```

### Reset the database (dev only)

Use this if migrations fail mid-run (e.g. `DuplicateObjectError`, `UndefinedTableError`, or any partial state):

```bash
# 1. Wipe the DB — drops all tables, types, enums
docker exec -it mm-ecommerce-postgres-1 psql -U mm_user -d mm_ecommerce \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 2. Reset Alembic's migration state (it thinks migrations ran — tell it they didn't)
cd apps/api && alembic stamp base

# 3. Run all migrations from scratch
alembic upgrade head

# 4. Optional: re-seed sample data
python -m scripts.seed_db
```

> If `mm-ecommerce-postgres-1` is not your container name, check with `docker ps`.

Key tables: `users`, `categories`, `products`, `product_variants`, `carts`, `cart_items`, `orders`, `order_items`, `addresses`, `promo_codes`, `refresh_tokens`.

---

## Project Structure (detail)

```
apps/web/
├── app/                   Next.js App Router pages
│   ├── (home)/            Homepage
│   ├── [category]/        Category listing + product detail
│   │   └── [product]/     Product detail page (PDP)
│   ├── search/            Search results
│   ├── cart/              Cart page
│   ├── checkout/          Checkout flow
│   ├── account/           Account settings, orders
│   ├── privacy/           Privacy policy
│   └── terms/             Terms & conditions
├── components/
│   ├── layout/            Header, Footer, Navigation
│   ├── category/          ProductCard, ProductGrid
│   └── ui/                Button, Input, Toast, Breadcrumb, etc.
└── lib/
    ├── api.ts             Typed API client
    ├── cart-context.tsx   Cart state (React context)
    ├── types.ts           TypeScript interfaces
    └── analytics.ts       Umami analytics helpers

apps/admin/
├── app/(dashboard)/       Dashboard, orders, products, categories, users
└── components/            Admin-specific UI (forms, tables, badges)

apps/api/
├── app/
│   ├── api/v1/            Route handlers (one file per resource)
│   ├── core/              Config, DB session, exceptions, auth, limiter
│   ├── middleware/        Request ID, logging
│   ├── models/            SQLAlchemy ORM models
│   ├── schemas/           Pydantic request/response schemas
│   ├── services/          Business logic layer
│   └── templates/emails/  Jinja2 email templates
└── alembic/               Migration scripts
```

---

## Design System

| Token | Value |
|-------|-------|
| Primary colour | `#8a5a64` (dusty mauve) |
| Secondary | `#d6acab` |
| Tertiary | `#dfbdc1` |
| Display font | Playfair Display |
| Body font | Jost |

Tailwind v4 CSS-first config is in `apps/web/app/globals.css` inside `@theme {}`. Dark mode uses class-based toggling (`dark` class on `<html>`).

---

## Contributing

1. Create a feature branch from `main`
2. Follow existing code patterns (RSC for data fetching, client components only where interactivity is needed)
3. Run `pnpm lint` before committing
4. Keep API and frontend changes in a single logical commit where possible
5. Open a PR against `main`
