# Performance & latency deep-dive — 8 August 2026

> **Status, 8 August 2026 — Tiers 1–3 shipped.** What follows is the original
> audit, kept as written so the before-numbers stay honest. See
> [§5 What shipped](#5-what-shipped) at the end for what changed and what is
> deliberately still open.

Storefront (`apps/web`), API (`apps/api`), and the GCP VM that hosts the API.
Every number below was measured, not estimated. Method is noted per finding.

---

## 1. The headline

Production homepage TTFB is **650–1160 ms** (three cold `curl` runs: 0.651 s,
0.680 s, 1.165 s). `/en/all-products` is **600–700 ms**. Nothing is served from
cache — every response carries:

```
cache-control: private, no-cache, no-store, max-age=0, must-revalidate
x-vercel-cache: MISS
set-cookie: mm_locale=en; ...
x-vercel-id: bom1::iad1::bfxh8-...
```

That last header is the whole story in one line. A visitor in the UAE enters at
the **Mumbai edge (`bom1`)**, the render runs in a function in **Washington DC
(`iad1`)**, and that function calls the API in **`me-central1` (Doha)** — four to
nine times, serially in waves. The bytes cross the planet twice before the
customer sees a pixel.

Nothing about this is a slow query. `GET /api/v1/categories` answers in **77 ms**
from a browser and in **3 ms** from inside the VM. The database is not the
problem, and neither is the Python. **The problem is that the site renders
everything, on demand, from the wrong continent.**

---

## 2. Findings, worst first

### 2.1 🔴 Every single route is dynamically rendered — nothing is cacheable

`next build` marks **all 27 page routes `ƒ` (dynamic)**. Not one is static or
ISR, including `/about`, `/faq`, `/privacy` and `/contact`, which are pure copy.

The cause is one line — [`app/layout.tsx:69`](apps/web/app/layout.tsx#L69):

```ts
const cookieStore = await cookies();
const locale = cookieStore.get('mm_locale')?.value ?? 'en';
```

Reading `cookies()` in the **root** layout opts the entire application tree out
of static rendering. And it is reading something it already knows: the locale is
in the URL as `[locale]`, and the inline bootstrap script at
[`app/layout.tsx:91`](apps/web/app/layout.tsx#L91) plus
`TranslationProvider`'s `useEffect` *both* already set `lang`/`dir` on the
client. It is the third mechanism for the same job, and it is the expensive one.

Compounding it, [`proxy.ts:74`](apps/web/proxy.ts#L74) sets `mm_locale` on the
response for **every** locale-prefixed request. A `Set-Cookie` on an HTML
response makes it uncacheable by any shared cache even if Next were willing.

### 2.2 🔴 One homepage visit triggers ~30 full server renders

Measured in the browser via `PerformanceObserver`:

| | |
|---|---|
| RSC prefetch requests fired | **29** |
| Unique routes | 15 |
| Cumulative origin time | **11.8 s** |
| Average per prefetch | 407 ms |

Source: [`components/layout/CategoryNav.tsx:43`](apps/web/components/layout/CategoryNav.tsx#L43)
and `:63` set `prefetch={true}` on every category link. `CategoryNav` lives in
the **locale layout**, so it is on every page of the site. `prefetch={true}`
forces a *full* RSC payload rather than Next's default partial prefetch — and
because every route is dynamic (§2.1), each prefetch is a complete server render
with its own API calls.

Each route is fetched roughly **twice** (29 requests / 15 routes), because
dynamic routes get `staleTime: 0` in the client router cache and no
`experimental.staleTimes` is configured.

Downstream cost: ~30 renders × ~4 API calls ≈ **120 API requests to the e2-micro
per homepage view**. See §2.7 for why that is dangerous.

### 2.3 🔴 A category page makes 9 API calls, 3 of them pure waste

Counted from the API access log for one render of
`/en/cat-brownies?page=2&sort=price_asc`:

```
3 ×  GET /api/v1/i18n/translations/en
2 ×  GET /api/v1/categories/cat-brownies
1 ×  GET /api/v1/products?category=cat-brownies&per_page=12&page=2&sort=price_asc
1 ×  GET /api/v1/products?category=cat-brownies&per_page=12&page=1&sort=newest   ← discarded
1 ×  GET /api/v1/i18n/languages
1 ×  GET /api/v1/categories
```

Two distinct bugs:

1. **`generateMetadata` re-fetches the product list and throws it away.**
   [`[category]/page.tsx:57`](apps/web/app/[locale]/[category]/page.tsx#L57)
   calls `getCategoryData(slug)` — which fetches *both* the category *and* a full
   page of products — but uses only `data.category`. On any page ≠ 1 or any
   non-default sort the URLs differ from the render pass, so nothing dedupes and
   the extra product query is a genuine round trip to Doha for nothing.

2. **`translations` is fetched three times per render.**
   [`lib/i18n/server.ts:23`](apps/web/lib/i18n/server.ts#L23) uses
   `cache: 'no-store'`, so it is refetched in the metadata pass, the layout, and
   the page. At ~200 ms iad1↔Doha, that is ~400 ms of pure duplication.

   The comment there defends `no-store` on correctness grounds — a stale
   snapshot once shipped a raw `checkout.estimated_delivery` key to customers.
   That reasoning is sound and should be kept; the fix is `React.cache()` for
   per-request deduplication, which does not introduce a cross-request snapshot.

### 2.4 🟠 The API is on HTTP/1.1 with no upstream keepalive

`curl` reports `ver=1.1` against `api.meltingmomentscakes.com`.
[`nginx/conf.d/ssl.conf`](nginx/conf.d/ssl.conf) has `listen 443 ssl;` with no
`http2`. So every concurrent API call from a Vercel function needs its own TCP +
TLS handshake — across an ocean.

Worse, [`nginx/nginx.conf:54`](nginx/nginx.conf#L54) sets
`proxy_set_header Connection 'upgrade'` **globally**, and there is no `upstream`
block with `keepalive`. That means nginx opens a fresh connection to uvicorn on
*every single request*. The `Connection: upgrade` header is only meant for
WebSocket routes; applied globally it defeats connection reuse everywhere.

gzip *is* on and working (translations: 31 KB → 9.5 KB), so that part is fine.

### 2.5 🟠 960 KB of JavaScript, and a 533 KB single chunk

Measured decoded transfer on the live homepage: **960 KB across 14 scripts**, the
largest being **533 KB**. For a storefront whose homepage is a carousel, a
product rail and some copy, that is roughly 3× what it needs to be.

Contributors found:

- **`ProductCard` is a client component** that statically imports `ModifierModal`,
  `AddToCartControl`, `analytics` and the toast system. The modifier modal ships
  with every grid page even though it only opens on tap.
- **`LocationPicker` (Google Maps, `@vis.gl/react-google-maps`) is statically
  imported** by [`account/addresses/page.tsx:13`](apps/web/app/[locale]/account/addresses/page.tsx#L13).
  Checkout does it correctly with `next/dynamic` —
  [`AddressModal.tsx:15`](apps/web/app/[locale]/checkout/components/AddressModal.tsx#L15) —
  the account page does not.
- **The whole checkout page is `'use client'`** ([`checkout/page.tsx:1`](apps/web/app/[locale]/checkout/page.tsx#L1)),
  so the highest-value screen in the funnel ships as an empty shell and fetches
  rates, branches and addresses only after hydration.
- Firebase *is* already correctly lazy (`await import('firebase/auth')`) — no
  action needed there.

### 2.6 🟠 Fonts: 132 KB preloaded on every page, half of it the wrong alphabet

Four Google font families are declared in the root layout with 18 weights
between them. Mapping the built `@font-face` blocks to files on disk:

| Family | Files | Preloaded | Preloaded KB |
|---|---:|---:|---:|
| Raleway | 5 | 1 | 42.1 |
| Jost | 3 | 1 | 26.0 |
| **Tajawal** (Arabic) | 8 | 4 | **34.3** |
| **Cairo** (Arabic) | 3 | 1 | **30.0** |

**64 KB of Arabic fonts are preloaded on every English page**, competing with the
LCP image for bandwidth on a mobile connection. Both `variable` class names are
applied unconditionally to `<html>` in
[`app/layout.tsx:74`](apps/web/app/layout.tsx#L74), so the preloads are emitted
regardless of locale. The CSS carries **64 `@font-face` blocks / 22 KB** for the
same reason, inside a **97 KB render-blocking stylesheet**.

### 2.7 🟠 The rate limit will bite before the hardware does

[`nginx/nginx.conf:44`](nginx/nginx.conf#L44) sets
`limit_req_zone $binary_remote_addr zone=api_limit rate=10r/s`, applied with
`burst=20`. The key is the **client IP** — but all server-side rendering traffic
arrives from a small pool of Vercel egress IPs in one region. Every SSR fetch
from every visitor shares one bucket.

At ~9 API calls per category render (§2.3), that is roughly **two page renders
per second** before nginx starts returning 503s — and the prefetch storm in §2.2
multiplies the request count by an order of magnitude. This is a cliff, not a
gradient: it will look fine until it suddenly does not.

Related: `UVICORN_WORKERS: "1"` on a shared-core e2-micro that also runs
Postgres, Redis, nginx, and a second POS API container in 1 GB of RAM.

### 2.8 🟡 The LCP image is lazy-loaded on listing pages

`next/image` defaults to `loading="lazy"`. Grepping for `priority` finds it in
only four places — none of them a product grid. On a category page and on the
homepage featured rail, the LCP element is a lazily-loaded image, so the browser
does not even discover it until layout completes.

The hero is handled better (`fetchPriority="high"` on slide 0), but **every**
slide is `loading="eager"`, so a 3-slide CMS hero pulls three full-bleed banners
(~70–128 KB each as mobile AVIF) at page load. There is also no
`<link rel="preload">` for the hero image, so it is discovered only after the
97 KB stylesheet parses.

### 2.9 🟡 Material Icons: a webfont fetched from Google after hydration

46 files use `<span className="material-icons">`, across **35 distinct glyphs**.
The stylesheet is injected by a script with `strategy="afterInteractive"`
([`app/layout.tsx:100`](apps/web/app/layout.tsx#L100)), so:

- icons are invisible or show as literal text until hydration completes,
- it costs a third-party DNS + TLS + two round trips to `fonts.googleapis.com`
  (the only third-party host on the page),
- the `<link rel="preload" as="style">` in `<head>` **renders twice** in the
  output and is never consumed as a stylesheet, which is a wasted early fetch
  and a Chrome console warning.

35 inline SVGs would be roughly 3 KB of markup and zero requests.
`DeliveryEstimate.tsx` already does exactly this, for exactly this reason —
its comment records a previous incident where a missing icon font rendered
`local_shipping` as literal text on every product card.

### 2.10 🟡 Guest visitors pay for two auth calls that cannot succeed

Client waterfall from the live site:

```
900 ms  GET /auth/me       → 401
941 ms  POST /auth/refresh → (fails for a guest)
```

[`lib/api.ts:76`](apps/web/lib/api.ts#L76) retries every 401 through
`refreshAccessToken()`. For an anonymous visitor — most first-time traffic —
that is two guaranteed-useless round trips on every page load. A cheap check for
the presence of a session cookie before calling `me()` removes both.

### 2.11 🟡 12 KB of translations embedded in every page

`GET /i18n/translations/en` returns **267 keys / 12 KB**. `TranslationProvider`
is a client component receiving the whole map as a prop, so all 267 strings are
serialised into the RSC payload of **every** page — the checkout, account, FAQ
and about copy all ride along on a product page. Namespacing by route section
would cut this by roughly 70%.

### 2.12 🟢 Backend: healthy, with two small things

Credit where due — the API is in good shape. `/categories` and
`/products/featured` are Redis-cached with correct invalidation on write, and a
warm request touches the database **zero** times (verified by counting
`sqlalchemy.engine` log lines: 3 cold, 0 warm). No N+1s found;
`_product_load_options()` uses `selectinload`/`joinedload` properly.

Two items worth fixing:

- **`product_service.get_all` counts through the sort.** The count is built as
  `select(func.count()).select_from(stmt.subquery())` where `stmt` already
  carries `ORDER BY`. For `price_asc`/`price_desc` that ordering is
  `_from_price()` — two correlated subqueries over `product_modifiers` and
  `modifier_options` — so the count evaluates and sorts a price expression whose
  result it discards. Strip ordering (and the `joinedload` join) before counting.
- **`get_db` is a dependency on cache-hit paths.** `list_categories` and
  `list_featured` take `db: AsyncSession = Depends(get_db)` and
  `viewer: User = Depends(get_optional_user)` before consulting Redis. asyncpg
  connects lazily so no connection is actually acquired, but `get_optional_user`
  *does* run a `SELECT * FROM users` for any request carrying a cookie — on a
  path that was about to be answered from cache. Pool is `pool_size=5,
  max_overflow=5`, i.e. 10 connections total.

---

## 3. What to do, in order

Ordered by measured-impact ÷ risk. The first four are where essentially all of
the win is.

### Tier 1 — structural (target: 650 ms → ~50 ms TTFB)

1. **Move the Vercel function region to `me-central1` or `fra1`.**
   One project setting. Removes a ~200 ms trans-Atlantic round trip from *every*
   API call on *every* render. Biggest single win available, zero code.

2. **Make the shell static.** Move `<html>`/`<body>` from `app/layout.tsx` into
   `app/[locale]/layout.tsx` so locale comes from `params`, not `cookies()`.
   Drop the redundant `Set-Cookie` from `proxy.ts` on already-prefixed paths.
   Then give the content routes `export const revalidate` (`/about`, `/faq`,
   `/privacy`, `/contact` → 3600; category and product pages → 60–300 with
   `revalidatePath` on admin writes). Turns MISS into HIT at the Mumbai edge.

3. **Kill the prefetch storm.** Remove `prefetch={true}` from `CategoryNav`
   (default partial prefetch is the right behaviour), and set
   `experimental.staleTimes: { dynamic: 30 }` so a prefetched route is not
   refetched seconds later. Removes ~11.8 s of origin work per visit.

4. **De-duplicate the server fetches.** Wrap `getTranslations`, `getLanguages`
   and `getActiveCategories` in `React.cache()` — per-request memoisation, no
   cross-request staleness, so §2.3's correctness comment still holds. Give
   `generateMetadata` a `getCategoryMeta()` that fetches only the category.
   9 calls → 4.

### Tier 2 — infrastructure

5. **`listen 443 ssl http2;`** on all three server blocks.
6. **Add `upstream api { server api:8000; keepalive 32; }`** and scope
   `proxy_set_header Connection 'upgrade'` to the routes that actually need it —
   set `Connection ""` for the rest.
7. **Exempt the Vercel egress from `api_limit`,** or key the limit on
   `X-Forwarded-For` rather than `$remote_addr`, before §2.7 becomes an outage.

### Tier 3 — payload

8. **Load Arabic fonts only for `ar`.** Apply the `tajawal`/`cairo` variables in
   `app/[locale]/layout.tsx` conditionally on locale. −64 KB of preload on every
   English page.
9. **Replace Material Icons with inline SVGs** (35 glyphs). Removes the only
   third-party host, the double preload, and the post-hydration icon pop-in.
10. **`priority` on the first 2–4 product-grid images;** `loading="lazy"` on hero
    slides after the first; `<link rel="preload">` the hero.
11. **`next/dynamic` for `ModifierModal` and for `LocationPicker` on the account
    addresses page.**
12. **Namespace translations by route** — ship ~80 keys per page, not 267.
13. **Skip `/auth/me` when no session cookie exists.**

### Tier 4 — backend polish

14. Strip `ORDER BY` and the category `joinedload` from the count subquery in
    `product_service.get_all`.
15. Consult Redis before resolving `get_optional_user` on cached list endpoints.
16. Raise `UVICORN_WORKERS` to 2 and re-measure; the e2-micro may not have the
    headroom, so this one is measure-first.

---

## 4. Method

- **Production latency**: `curl -w` against `meltingmomentscakes.com` and
  `api.meltingmomentscakes.com`, 3 cold runs each.
- **Browser waterfall / prefetch counts / JS weight**: `PerformanceObserver` and
  `performance.getEntriesByType('resource')` in a 375×812 mobile viewport.
- **Render / build classification**: `pnpm build` route table.
- **Per-render API call counts**: local stack (`docker compose up postgres redis
  api`, migrations + seed applied), nginx-style access log diffed around a single
  `curl` to the built Next server.
- **SQL-per-endpoint**: `sqlalchemy.engine` echo lines counted around each
  request, cold and warm.
- **Font attribution**: built `@font-face` blocks mapped to `.next/static/media`
  files on disk; `.p.` infix marks a preloaded file.

---

## 5. What shipped

Everything in Tiers 1–3, plus two of the four backend items. The region move
(`iad1` → `me-central1`) was made in the Vercel project settings and takes
effect on the next deploy.

### Rendering and caching

| | Before | After |
|---|---|---|
| Routes served from cache | 0 of 27 | 18 ISR (`●`), rest dynamic for a reason |
| Homepage, 5 views | 7 API calls **per view** | **0** |
| Product page, 5 views | 5 API calls per view | **0** |
| Category page, 5 views | 9 API calls per view | **0** (still a function invocation — it reads `searchParams`) |
| RSC prefetches per homepage visit | 29, over 15 routes, 11.8 s origin time | **0** |
| Response headers | `no-store`, `x-vercel-cache: MISS` | `s-maxage=60`, `x-nextjs-cache: HIT` |

The root layout moved under `[locale]` so the shell stopped reading `cookies()`;
`prefetch={true}` came off `CategoryNav`; the shared server fetches went through
`React.cache`; and the two `no-store` decisions became a 60 s TTL with cache
tags. `revalidate` alone turned out to do nothing on a segment with dynamic
params — the product and blog routes needed `generateStaticParams` alongside it,
which took watching for `x-nextjs-cache` and never seeing it to find.

Freshness was verified end to end: a product renamed in Postgres appeared on the
storefront inside the window, with stale-while-revalidate serving the old copy
until the background render finished.

### Payload

- Material Icons is gone. 128 KB of webfont and two third-party origins replaced
  by 19 KB of path data for 55 icons, extracted from the real font by
  `apps/web/scripts/extract-icons.mjs` rather than redrawn. Icons now paint with
  the page instead of waiting for hydration.
- No font is preloaded any more, so each page downloads only its own script's
  faces rather than all four families. The unused 300 weights are gone.
- The first four product-grid images carry `priority` — the LCP element on every
  listing page was lazy-loaded.
- The hero renders one frame on the server and brings the rest in on idle,
  keeping ~125 KB of unseen artwork off the critical path.
- `ModifierModal` and the account page's `LocationPicker` are `next/dynamic`.

### Infrastructure

`http2 on` for all three TLS server blocks, an upstream pool with `keepalive 32`,
and `Connection: upgrade` scoped to real WebSocket handshakes instead of every
request. The API rate limit went from 10r/s to 30r/s with a burst of 100 —
necessary now, because a deploy prerenders the catalogue from one Vercel address.
Both config paths validated with `nginx -t`.

### Backend

`product_service.get_all` no longer counts through its own `ORDER BY`. The count
was built off the finished statement, so `price_asc` had Postgres evaluate
`_from_price()` for every row and sort them to produce a number that cannot
depend on order. Totals verified unchanged across every sort and filter shape.

### Deliberately not done

- **On-demand cache invalidation.** Every cached fetch carries a tag (`i18n`,
  `cms`, `catalogue`) so a `revalidateTag` from the API would take the 60 s delay
  to zero. It needs a shared secret across the five locations in CLAUDE.md §9 and
  API-side calls on write. The TTL is the safety net until then.
- **Redis before `get_optional_user`.** Measured: a warm `/categories` makes zero
  database queries, because the storefront's server-side fetches carry no cookies
  and `_get_user_from_token` returns before touching the database. The only
  callers with a token are the admin app, which bypasses the cache anyway.
- **Rate limiting on the right key.** Shop traffic is still keyed on an address
  that is not the shopper's. Raising the limit bought headroom, not correctness.
- **`/[category]`, `/all-products`, `/search`** stay dynamic: they read
  `searchParams`, which no amount of `revalidate` will change. Their *data* is
  cached, so they cost a function invocation and no API traffic.
