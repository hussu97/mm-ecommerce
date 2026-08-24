# Architecture & System-Design Audit — August 2026

> **Status: remediated.** Every P0, P1 and P2 item below, and most of P3, was
> implemented over 2026-08-15 — see `tasks/todo.md` for the commit-by-commit
> record and the decisions taken where the audit's recommendation was refined
> in contact with the code. The findings are kept in full, in their original
> wording, because the *reasoning* is what stops the same drift returning; the
> conventions they produced now live at the top of `CLAUDE.md`.
>
> Three things this audit did not predict, found while fixing it:
> `alembic downgrade base` had been broken for months (four revisions restored
> dropped columns without their indexes or keys); the sixth copy of the
> permission helper was dead code enforcing nothing; and cancelling a counter
> sale from the console restocked ingredients it had never claimed.

**Scope:** full-repo audit of `apps/api` (FastAPI + SQLAlchemy async), `apps/web` (Next.js 15 storefront), `apps/admin` (Next.js 15 dashboard), `packages/*`, DB models (48 files) and the Alembic chain (96 revisions). Conducted by five parallel deep-read audits (backend architecture, DB layer, storefront, admin, cross-app contracts).

**Why this audit exists:** the codebase has grown a lot of functionality, and changes — especially AI-assisted ones — increasingly go wrong because data flows and state changes don't follow consistent patterns. This document identifies the root causes and lays out a prioritized remediation plan.

---

## Executive summary

The codebase is in better shape than the symptom suggests: money is uniformly `Numeric` (zero Float), all timestamps are tz-aware, the migration chain is linear with no model drift, config discipline is airtight (zero `os.environ` reads in `app/`), analytics is centralized and in sync with its docs, and the code is unusually well-commented — many past incidents are written down at the exact line that fixed them.

The problem is not sloppiness. It is that **almost every convention has 2–5 coexisting variants, and nothing marks which one is authoritative**. That is precisely the substrate on which AI-assisted edits hallucinate: an agent pattern-matches from a neighbouring file and has a coin-flip chance of copying the wrong precedent. Three root causes account for the large majority of the ~65 findings:

1. **Order lifecycle authority is fragmented.** One state machine (`VALID_TRANSITIONS`), but five independent enforcers and ~13 write sites for `Order.status` — plus three sibling status columns (`pos_status`, `delivery_status`, `courier_status`) synchronized only by convention. The side effects of a transition (refund, restock, courier cancel, POS void) fire only on the admin path.
2. **Contracts are hand-transcribed, never generated.** No OpenAPI codegen exists. ~2,050 lines of hand-written TS types mirror 5,300 lines of Pydantic by eye, in four places. The two packages built to be the shared source (`@mm/types`, `@mm/ui`) are dead: one is stale-and-wrong, the other literally `export {}`. Money math (VAT, low-order fee, from-price) is re-derived client-side in float arithmetic.
3. **No canonical per-app pattern for fetch / state / errors.** Web has four ways to call the backend; admin has two parallel copy-pasted fetch stacks and four data-loading patterns; the API has thin-router and fat-router styles side by side, two error dialects, two commit conventions, and two email-dispatch policies with contradictory in-code justifications.

**One finding needs action before all others:** an order marked undelivered by the *courier webhook* never triggers the automatic refund (that logic lives only on the admin path) — yet the same webhook sends the customer an email promising a refund. That is a live, silent, money-losing gap (P0-1 below).

---

## The one production bug to fix first

### P0-1 · Courier-marked UNDELIVERED never refunds, but emails a refund promise — **Critical, money-losing**

- `order_service.update_status` owns the consequences of a transition: `_REFUNDABLE_ENDINGS = {CANCELLED, UNDELIVERED}` → automatic `payment_service.refund_order` (`apps/api/app/services/orders/order_service.py:1487-1494`), plus courier-cancel, batch-cancel, POS void, and restock on cancellation (`order_service.py:1443-1474`).
- But the noon Send webhook bypasses it: `noon_send_service.py:1026-1032` assigns `order.status = OrderStatusEnum.UNDELIVERED` **directly** — no refund is attempted and nothing queues one for a human.
- The same path then calls `email_service.notify_order` (`noon_send_service.py:1037`) → `send_order_undelivered` → `_refund_context` (`email_service.py:791-823`), which for any paid card order renders "your refund is on its way" — for a refund that was never initiated.
- Same asymmetry: `pos_order_service.void_order` / `join_orders` set `"cancelled"` directly and skip restock.

**Fix:** part of P0-2 below (transition function owns consequences). If you want a surgical hotfix first: route the courier services' terminal transitions through `order_service.update_status` instead of direct assignment. Also audit production for paid orders in `undelivered`/`cancelled` with `refunded_amount = 0` that received a refund email.

---

## P0 — Correctness: one authority for order state (fix now)

These are one coherent refactor of the backend order lifecycle plus three storefront state bugs. They directly target the reported symptom ("state changes are all over the place").

### P0-2 · One transition function that owns validation AND consequences — **Critical**

`Order.status` is written from ~13 places; `VALID_TRANSITIONS` (`order_service.py:88-159`) binds only the admin path. The five current enforcers have already drifted into **contradiction**: `order_service.py:124-141` declares UNDELIVERED terminal (only `{REFUNDED, DISPUTED}` out), but `noon_send_service.py:1003-1012` still allows UNDELIVERED → OUT_FOR_DELIVERY with a comment asserting the opposite policy, while Lalamove's third copy (`lalamove_service.py:1344-1348`) does not. Checkout writes status with no validation (`payment_service.py:221,235,265`); POS writes **raw string literals** (`pos_order_service.py:1163,1203,1301` — `order.status = "confirmed"`), which already caused the `previous_status = '4'` production bug documented at `order_status_event.py:211-232`.

**Fix (in order):**
1. Create `order_lifecycle.transition(db, order, new_status, *, source, allow=...)` — checks `VALID_TRANSITIONS`, assigns the enum, and carries the consequences (refund, restock, courier/batch cancel, POS void, publish-to-register, packed-dispatch) keyed off the *transition*, not the endpoint. Callers with legitimately special policies (courier redelivery, POS counter-sale) pass explicit `allow` flags instead of maintaining private guard sets (`_CONFIRMABLE_FROM`, the two courier guards).
2. Migrate all 13 write sites; delete the per-module guards.
3. Enforce it structurally: the `Order.status` attribute listener (`order_status_event.py:235` — which already journals every write) additionally validates transitions and raises/logs loudly outside the map; plus a unit test that greps the AST for `.status =` assignments on `Order` outside `order_lifecycle`.

Effort: ~2-4 days. No migration. Expect it to flush out existing illegal writers — that is the point.

### P0-3 · One owner for the four status columns — **High**

`orders.status` (enum), `orders.pos_status` (String), `orders.delivery_status` (String), `order_deliveries.courier_status` (String) are synchronized only by convention; production has already shipped `cancelled` + `pos_status='active'` desync (documented at `order_service.py:1451-1461`).

**Fix:** the transition function from P0-2 owns all pairings (`cancelled ⇒ pos_status=void`, etc.). Then add CHECK constraints for known-impossible combinations, e.g. `NOT (status='cancelled' AND pos_status='active')` — one additive migration; count/clean violating rows first.

### P0-4 · Storefront: promo total can be stale on the Place Order button — **High**

Promo state lives in four places (cart page state → `sessionStorage['mm_checkout']` → checkout form → `PromoCodeStep`). Re-validation on subtotal/identity change lives *only* inside `PromoCodeStep` (`PromoCodeStep.tsx:133-141`), which is mounted only after the customer opens the "add promo or note" fold-out (`checkout/page.tsx:1516-1547`). Change the basket in another tab and the button quotes a total the server won't charge. Promo application logic is also implemented twice with different identity payloads (`cart/page.tsx:155-220` vs `PromoCodeStep.tsx:65-119`).

**Fix:** one `usePromoValidation` hook used by cart and checkout; re-validation runs whenever `form.promoDiscount > 0`, independent of the fold-out.

### P0-5 · Storefront: cart/auth lifecycle bugs — **High**

- Error "rollback" in `cart-context.tsx:66-117` restores a closure snapshot; with two in-flight mutations it rewinds newer server state. Fix: `refreshCart()` on error, delete the `prev` pattern.
- Cart never watches `user`: merge-on-login exists only because two pages remember to call it; on logout nothing refreshes, and `clearSessionId()` is dead code (`api.ts:48`, zero callers) — the previous user's `mm_session_id` survives on shared devices. Fix: `useEffect` on `user` inside `CartProvider` (merge on sign-in; clear session id + refresh on sign-out); delete per-page calls.
- `mergeCart` failure is swallowed twice (`cart-context.tsx:133-135` + `.catch(() => {})` at the call sites).

---

## P1 — Single sources of truth (the anti-hallucination layer)

These changes remove the "four plausible definitions, none marked authoritative" problem. P1-1 mechanically prevents most contract drift from recurring.

### P1-1 · OpenAPI → TypeScript codegen into `@mm/types` — **High, highest leverage**

Today: zero codegen; web `lib/types.ts` (623 ln), admin `lib/types.ts` (864 ln) + `pos-types.ts` (565 ln) hand-mirror `apps/api/app/schemas/` (5,316 ln). `@mm/types` is imported by **nothing** and is actively wrong (`OrderStatus` has 4 of 10 values; `PaymentProvider` lists `stripe|tabby|tamara` when the live set is card/cod via Stripe/Ziina; `PaginatedResponse` uses `page_size/total_pages` vs the real `per_page/pages`).

Concrete drift already shipped:
- Admin `OrderStatus` missing `payment_failed`, `refunded`, `disputed` (`admin/lib/types.ts:168-179`) — the admin performs refunds but can't filter or badge the status it creates; badges render `undefined` variant.
- Admin `Order` missing `low_order_fee`, `email_has_account`, `locale` (vs `schemas/order.py:91`).
- Web `AddressCreate` makes `latitude`/`longitude` optional (`web/lib/types.ts:413-414`) while Pydantic requires them (`schemas/address.py:20-21`) — the whole zone-pricing model depends on the pin; the TS contract invites a 422 or a "fix" that breaks zone pricing.
- `tracking_by_sms` (`schemas/fulfilment.py:92`), `pricing_method`, `nutrition` (`schemas/product.py:132,141`) exist on the wire and in **no** frontend type — features half-shipped and invisible to autocomplete.

**Fix:** `openapi-typescript` generating into `packages/types` as a turbo task; both apps add real `workspace:*` deps (today the turbo dependency graph is empty where it matters — admin maps no shared paths at all); CI freshness check diffing generated output against `/openapi.json`, in the spirit of the existing `test_compose_env_allowlist.py`. Migrate app code file-by-file; delete the hand-written mirrors as they empty out. Effort: 1-2 days to stand up, then incremental.

### P1-2 · Server-authoritative order totals: a `POST /orders/preview` endpoint — **High**

Money math is currently derived in five places that must agree:
- Backend web engine: `order_service.py` with **hardcoded** `VAT_RATE = Decimal("0.05")` (`order_service.py:84-85`).
- Backend POS engine: `pos_pricing.calculate_order` — tax-table driven, inclusive/exclusive aware. Both write the *same* `orders.subtotal/total/vat_amount` columns.
- Checkout client: grand total computed twice in the same file with different inputs (`checkout/page.tsx:852-854` vs `OrderSummary` at `:443-447`), plus a third VAT formula in float (`(subtotal - discount) * 5 / 105`, `:597`) that ignores fees — the printed VAT is not 5/105 of the printed total.
- `lowOrderFeeFor` (`checkout/page.tsx:134-147`) self-describedly "mirrors `low_order_fee_for` on the server" (`order_service.py:433`).
- `computeFromPrice` (`web/lib/pricing.ts:14`) mirrored as SQL in `product_service.py:41` — **with divergent fallbacks**: web takes the first modifier group in array order, SQL takes `func.min`, so the catalogue can sort by a price the card doesn't show.

**Fix:**
1. Unify the two backend engines on `pos_pricing.calculate_order` (pure, tested, tax-group aware); `order_service` feeds it `LineInput`s and keeps orchestration. Delete the hardcoded `VAT_RATE`. (Also DB F5: web money is `Numeric(10,2)`, POS money `Numeric(12,2)` on the same table — reconcile when convenient.)
2. Add `POST /orders/preview` returning the exact totals an order would be written with (the delivery-quote endpoint already establishes this pattern). Checkout renders, never computes.
3. Return `from_price` on `ProductResponse`; reduce `computeFromPrice` to a fallback or delete.

### P1-3 · Constrain the status vocabulary in the DB — **High**

Beyond the three native PG enums, every other status column is an unconstrained `String` (`pos_status`, `order_items.status`, `payment_transactions.status`, `custom_orders.status`, `delivery_batches.status`, `tills.status`, inventory/PO/kitchen statuses…). A typo'd status persists silently and disappears from every `WHERE status = ...`. Near-collision vocabulary exists (`CANCELED` vs `CANCELLED` across courier enums — deliberate provider-verbatim, but one keystroke from a bug).

**Fix:** standardize on **String + CHECK constraint** for internal lifecycles (cheap to alter, unlike `ALTER TYPE`); leave provider-verbatim columns (`courier_status`, webhook fields) unconstrained by design, with a comment saying so. One additive migration. Longer term, migrate the three native enums to the same pattern — three mechanisms is two too many.

### P1-4 · One fetch client per app (and per side) — **High**

- **Web:** four ways to call the backend live in one mixed module — client `request()` with session header/401-retry/error-analytics (`web/lib/api.ts:66-136`); two raw-fetch bypasses in the same file (`trackApi.lookup` at `:345-358`, `refreshAccessToken` at `:54-62`) that skip everything the file's own comment promises; server-side `RSC_API_BASE`/`fetchJson`; and `cmsApi.getPage` — a server-only function inside the client module. The `API_BASE` vs `RSC_API_BASE` footgun already caused a documented build hang (`api.ts:7-17`). **Fix:** split `lib/api-client.ts` (`import 'client-only'`) / `lib/api-server.ts` (`import 'server-only'`); route `trackApi` through `request()`.
- **Admin:** `lib/pos-api.ts:15-43` copies `lib/api.ts:46-89` nearly verbatim with divergent error unwrapping (object `detail` renders `[object Object]` on the POS side) and a second `refreshAccessToken` that can race the first on concurrent 401s. Its header comment claiming it "reuses the shared fetch wrapper" is false. **Fix:** export `request` from `api.ts`; delete the copy.
- **Base URLs:** three strategies; admin hardcodes absolute `http://localhost:8000/api/v1` (`admin/lib/api.ts:20`), going cross-origin in dev while its own rewrite (`admin/next.config.ts:33-38`) sits dead. **Fix:** align admin to web's relative-path + rewrite pattern.
- Longer term: one shared `createApiClient({ baseUrl, onError })` in a package serving web + admin + admin-POS (the register's device-token scheme stays intentionally separate — document that).

### P1-5 · Decide the fate of `@mm/ui` and `packages/config` — **Medium**

`packages/ui/src/index.ts` is `export {};` ("implemented in Prompt 8" — never was) while web keeps 16 components in `components/ui/` and admin 10 in a 359-line `components/ui/index.tsx`; at least five (Button, Input, Select, Badge, Spinner) exist twice with different props. Half-existing is the worst state: docs advertise it, an agent will import `{}` from it. `packages/config/src/delivery.ts` is worse — unused *and* repudiated (`FREE_DELIVERY_THRESHOLD = 150` "one number for the whole country" vs the per-zone model both apps now document; `FALLBACK_DELIVERY_FEE` re-declared locally at `web/app/[locale]/[category]/[product]/page.tsx:87`).

**Fix:** either populate `@mm/ui` for real (natural first tenants: `cn`, one `formatMoney(amount, locale)`, the Pagination component that embodies the CLAUDE.md 50/100/200/500/1000/2000 rule) or delete the package and correct CLAUDE.md/MEMORY.md. Delete `packages/config/src/delivery.ts`; have the product page fetch `/delivery/area` with an honest fallback. Reconcile the two contradictory `free_delivery_threshold` doc comments inside `admin/lib/types.ts` (:548 vs :755).

---

## P2 — Pattern standardization (make the right way the obvious way)

### P2-1 · Backend: routers do auth + schema mapping; state changes live in services — **High**

~130-150 of ~341 endpoints run queries/mutations inline in routers. The sharpest case: the purchase-order state machine lives **inline in the router** (`api/v1/inventory.py:689-733`) while the order state machine is a service and the transfer state machine is `transfer_service.py:123-311` — three homes for the identical pattern. `auth.py` (1,043 ln, ~31 direct DB calls) and `analytics.py` (952 ln) are the biggest fat routers.
**Fix:** adopt the rule "anything touching two tables or a status column is a service function"; write it in CLAUDE.md; apply opportunistically starting with `inventory.py` PO transitions and `auth.py`.

### P2-2 · Backend: one transaction convention — **High**

The clean default (request-scoped commit in `get_db`, `core/deps.py:28-37`) is undermined by: a dead duplicate `get_db` in `core/database.py:38-48` (delete it); unjustified mid-request commits in plain CRUD (`menu_group_service.py:195,221,303`); a commit inside an auth dependency (`api/v1/devices.py:112`); and courier services committing the *whole request session* mid-flight to protect an external booking (`lalamove_service.py:586,805`, `noon_send_service.py:622`) — defensible goal, wrong mechanism.
**Fix:** rule = "services flush, the request commits"; courier booking writes and `last_seen_at` stamps go on dedicated short sessions (the codebase already has this pattern in `email_service._log` and the webhook `Recorder`). `refund_order` gateway call inside an open transaction survives retry only by accident (`payment_service.py:933-945,1001-1017`) — make the idempotency-key safety designed, not incidental, by recording the refund attempt on its own session before calling the gateway.

### P2-3 · Backend: permission checks as route dependencies — **Medium**

The `_require` permission helper is copy-pasted into five routers (`pos_orders.py:137`, `inventory.py:81`, `operations.py:55`, `pos_reports.py:18`, inline `user.can()` in `tills.py:184`), ~40+ imperative call sites, and a forgotten check already shipped as a hole (documented at `pos_orders.py:243-246`).
**Fix:** one `require("pos.orders.void")` dependency factory used in route decorators — greppable, auditable, single definition.

### P2-4 · Backend: one email/notification policy + failure sweep — **Medium**

Two contradictory documented policies coexist: `orders.py:387-388` argues background tasks get dropped on serverless (inline-await policy), while `auth.py:344,893` uses `BackgroundTasks`. The never-raise funnel + `email_log` journaling is solid, but there is no retry: a Resend outage is permanent silence in a table nobody alerts on; and pre-commit sends can email about state that never commits.
**Fix:** declare inline-await the policy (delete the `BackgroundTasks` uses or justify them in place); move sends post-commit where feasible; add a small sweep over `email_log.status='failed'` in the existing scheduler loop — 80% of an outbox for 5% of the cost.

### P2-5 · Admin: one data-fetching abstraction — **High**

Four competing patterns (hand-rolled `useEffect` loads, fetch-all + client-slice, `ResourcePage`'s loader, a bespoke report hook) and four refetch-after-mutation styles. Client-side vs server-side pagination differs invisibly per page behind the same `Pagination` component; POS tables have no pagination at all; products search fires a request per keystroke with no debounce or abort (orders/customers debounce 350ms via copy-pasted blocks).
**Fix:** adopt SWR or React Query (or one `usePaginatedList`/`useResource` hook pair) with AbortController in the shared request layer and a `useDebouncedValue` hook; encode which endpoints paginate server-side. Promote `ResourcePage` (a genuinely good 429-line abstraction currently used by only the POS half) as the standard CRUD screen and migrate simple ecommerce pages (promo-codes first: 537 lines → ~60).

### P2-6 · Admin: error surfaces — **Medium**

41 `alert()`/`confirm()` call sites for mutations plus one hand-rolled toast (`translations/page.tsx`); two pages violate the app's own documented load-error rule (`categories/page.tsx:33-37`, `languages/page.tsx` — no `.catch`, no `LoadError`); `analytics/page.tsx:116-127` fires 10 endpoints in one `Promise.all` where one failure blanks the whole dashboard.
**Fix:** one toast/confirm provider; `LoadError` on the two pages; `Promise.allSettled` with per-section error states in analytics.

### P2-7 · Web: decompose the 1,621-line checkout — **High (after P0-4/P1-2 land)**

`CheckoutContent` holds 13 `useState` + 10 `useEffect` + 5 refs; `handleSubmit` has a 15-entry dependency array (`checkout/page.tsx:1171`). Extract `useCheckoutForm` (with sessionStorage persistence), `useDeliveryQuote`, `usePhoneVerification`, `useRetryOrder`; move `OrderSummary`/`PickupBranchPicker`/`UnserviceableNotice` to `components/`. Do this *after* the preview endpoint (P1-2) deletes the client money math — otherwise you refactor code that's about to disappear.

### P2-8 · Backend: one error dialect — **Low**

`AppError` hierarchy is dominant and good; six routers still raise bare `HTTPException` for identical cases (`orders.py` 19×, `delivery_zones.py` 19×, `uploads.py`, `pos_orders.py`, `payments.py`, `webhook_logs.py`). Routers raise `AppError` subclasses only; add a `payload` field to `AppError` for the structured-detail 409s.

---

## P3 — Hygiene (cheap, mostly additive migrations and cleanups)

| # | Item | Evidence | Fix |
|---|---|---|---|
| P3-1 | UUID-array "FKs" with no referential integrity (device/marketing/notification scoping) | `device.py:93-95`, `marketing.py:79,189-255`, `operations.py:175-180` | Service-level validation on write now; join tables when the admin UI is next touched |
| P3-2 | `business_date` is `String(10)` in 7 tables while sibling columns are `Date` | `order.py:250-252`, `till.py:61`, `inventory.py:424,546` … | Add `CHECK (business_date ~ '^\d{4}-\d{2}-\d{2}$')` now; defer type conversion |
| P3-3 | Mutable tables missing `updated_at` (`order_items`, `promo_codes`) | `order.py:376`, `promo_code.py:102-112` | Additive migration, backfill from `created_at`; rule: data-fix migrations bump `updated_at` |
| P3-4 | Missing FK indexes (`order_items.product_id`, user-audit columns) | migrations 001/003/024/035/041 | One additive migration: `ix_order_items_product_id` at minimum |
| P3-5 | `lazy="selectin"` on four `Order` collections fights the model's own loud-lazy philosophy (`noload` escape hatch at `order_service.py:1297`); costly at 2000/page | `order.py:321-342` | Drop `selectin`; callers opt in with `selectinload` |
| P3-6 | Percent vs fraction in look-alike columns (`vat_rate`=0.05 vs `fee_percent`=2.9) | `order.py:176-178`, `payment_gateway.py:124-126` | Naming/comment convention: `*_rate` = fraction, `*_percent` = percentage |
| P3-7 | Pydantic-validate the JSONB snapshots (`selected_options_snapshot` is `list[Any]` — the one place with two known wire dialects has no contract) | `schemas/cart.py:63`, `schemas/order.py:35`, `option_snapshot.py:55-57` | One `SelectedOptionSnapshot` model used by cart + order schemas; codegen fans it out |
| P3-8 | Soft-delete split: POS pairs `is_active`+`deleted_at`, catalog uses `is_active` only, nothing constrains agreement | `product.py:72` vs `branch.py` etc. | CHECK `deleted_at IS NOT NULL ⇒ is_active=false` on paired tables; document catalog as deactivate-only |
| P3-9 | Server phone validation is length-heuristics while the client runs libphonenumber — the *stricter* check is on the untrusted side, and phone is the promo-abuse identity | `core/phone.py:38-58` vs `PhoneInput.tsx:209` | Use Python `phonenumbers` server-side; client check stays as UX |
| P3-10 | i18n: dual `name_localized`+`translations` columns with Python fallback chains (already shipped one silent bug, `branch.py:174-183`); web's `t()` interpolation duplicated client/server and single-replace | `branch.py:49-52`, `i18n/server.ts:85-95` vs `TranslationProvider.tsx:35-46` | Declare `translations` authoritative; share one `interpolate()` with global replace |
| P3-11 ✅ | Caching: keys and invalidation split between routers and services; analytics busted only on web order creation (not webhooks/POS/refunds) | `products.py:121-151`, `orders.py:162` | **Done** — analytics is now TTL-only by declaration: the lone bust is deleted and the rule is written at `_ANALYTICS_TTL`. Busting all five writers would cost a Redis keyspace scan per counter sale to save a margin dashboard five minutes |
| P3-12 | Cache-policy drift at the web edges (hardcoded `revalidate`, string-literal tags vs `CACHE_TAGS`) | `i18n/server.ts:52,75`, sitemaps | Route all TTLs/tags through `cache-policy.ts` |
| P3-13 | Formatters: `formatPrice` defined and never used (28 hardcoded `` `${x.toFixed(2)} AED` `` in JSX, AED untranslated in Arabic); admin has two currency formats on adjacent screens | `web/lib/utils.ts:9-11`, `admin/lib/utils.ts:14` vs `ResourcePage.tsx:426` | One locale-aware `formatMoney` (see P1-5) |
| P3-14 ⚠️ | Migrations: 34+ revisions contain `op.execute` content edits (CMS copy, slug fixes, seeded passwords) that replay on fresh environments | versions 015/016/033/054-056/061/062 | **Recommendation reversed — do not follow the fix in this row.** "Content edits go in `scripts/`" was tried and was wrong: a script is only as good as somebody remembering to run it, and until they do the site keeps saying the wrong thing. Content the deploy has to carry is a **migration**, guarded so it cannot fight the admin — match the exact value being replaced, so that once a human edits it in the console the migration matches nothing. `CLAUDE.md` rule 7 is the current policy and this row is kept only for the reasoning that produced it |
| P3-15 ⚠️ | Admin auth: `TokenResponse` still types `access_token`/`refresh_token` no caller reads | `admin/lib/types.ts:11-16` | **Recommendation withdrawn after checking the wire.** The API *does* send both fields (`schemas/user.py:40-44`, set into httpOnly cookies server-side by `_set_auth_cookies`), so trimming the type would have manufactured the very type-vs-wire drift this audit exists to remove. The type now documents why it declares fields nobody reads. **A real security item fell out of it:** because the tokens ride in the body they are also in JavaScript's reach, which is most of what the httpOnly cookie was chosen to prevent. Dropping them from the three cookie-based endpoints costs web and admin nothing (neither reads them) but must not touch the register's bearer flow — a deliberate, separately reviewed change, not a passing edit |
| P3-16 | Web: 2 of 59 bare `catch {}` blocks hide real state (mergeCart; promo sessionStorage write dropping a discount silently) | `cart/page.tsx:267` | Surface both |

---

## What is healthy — do not "fix"

- **`OrderStatusEvent` capture**: the SQLAlchemy `set` listener + `acting_as()` contextvar journals *every* status write regardless of author (`order_status_event.py:235-305`). This is the foundation P0-2 builds on — extend it, don't replace it.
- **Webhook processing**: dedup via `INSERT … ON CONFLICT DO NOTHING` in-transaction (`payment_service.py:517-538`); `webhook_logs` on its own session so rows survive rollbacks. This is the good template for P2-2.
- **Idempotency/uniqueness work**: partial uniques on `order_payments.idempotency_key`, per-gateway `payment_transactions`, check-numbers; `refunded_amount` double-refund guard.
- **Config discipline**: `Settings` is the single source; zero `os.environ` in `app/`; the compose-allowlist regression test. Reuse this "convert remembered into enforced" pattern for every P1 item.
- **Schema fundamentals**: no Float money, no naive datetimes, uniform UUID PKs, reasoned `ondelete`, linear migration chain with no model drift.
- **Web server-side fetch semantics** (`fetch-json.ts` throw/null/empty distinctions, `HAS_REMOTE_API`) — deliberate and incident-driven.
- **Analytics**: one `track()`, deferred dispatch, and the Umami doc is 70/70 events in sync.
- **Deliberate table "duplication"**: `orders` vs `custom_orders`, `charges` vs `payment_transactions`, `webhook_events` vs `webhook_logs`, the courier/batch/delivery layering — all justified with written rationale. Don't consolidate these.

---

## Guardrails: keep it fixed

The repo already proves the winning pattern — `test_compose_env_allowlist.py` turned a five-location checklist into a failing test. Apply the same idea:

1. **AST test**: no `Order.status` assignment outside `order_lifecycle.py` (P0-2).
2. **CI codegen freshness check**: generated `@mm/types` matches the live `/openapi.json` (P1-1).
3. **Import-boundary lint**: `'client-only'`/`'server-only'` markers in the split web API modules (P1-4); ESLint ban on `alert`/`confirm` in admin (P2-6).
4. **CLAUDE.md conventions section** documenting the chosen canon (routers-vs-services rule, flush-vs-commit rule, email policy, which fetch client to use where, String+CHECK for statuses) — so agents copy the right pattern instead of inferring one from a neighbouring file.
5. **Grep tests for mirrors**: until P1-2 deletes them, a test asserting `lowOrderFeeFor`, `computeFromPrice`, and the client VAT line stay in sync with their server twins would have caught the from-price divergence.

---

## Suggested sequencing

| Phase | Items | Outcome |
|---|---|---|
| **Week 1** | P0-1 hotfix + production refund audit; P0-4; P0-5 | Money and customer-facing state bugs closed |
| **Weeks 2-3** | P0-2, P0-3 (one refactor); guardrail #1 | Single order-state authority; the reported symptom's root cause gone |
| **Weeks 3-4** | P1-1 codegen + guardrail #2; P1-4 fetch clients | Contract drift becomes structurally impossible; one request path per app |
| **Month 2** | P1-2 preview endpoint + engine unification; P1-3 status CHECKs; P1-5 package decisions | Money math server-authoritative; DB vocabulary constrained; dead packages resolved |
| **Ongoing/opportunistic** | P2 items as files are touched (P2-5 admin hook and P2-7 checkout decomposition first); P3 table as capacity allows | Each convention converges on one canonical variant |

Every P0/P1 item is independently shippable; nothing here requires a big-bang rewrite. The single highest-leverage day of work in this document is standing up the OpenAPI codegen (P1-1) — it retires four hand-written type files and prevents the largest class of future hallucination at the source.
