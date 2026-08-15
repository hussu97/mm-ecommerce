# P0 Remediation — from docs/architecture-audit-2026-08.md

Working set: all P0 items, fixing adjacent issues found along the way, keeping to the
system-design patterns the audit declares canonical (single transition authority,
services-flush/request-commits, AppError, enum members not string literals).

## Backend (apps/api)

- [ ] P0-2a: Create `app/services/order_lifecycle.py` — single `transition()` that
      validates against `VALID_TRANSITIONS`, assigns the enum, and owns consequences
      (refund, restock, courier/batch cancel, POS void, publish, dispatch).
- [ ] P0-2b: Route all ~13 `Order.status` write sites through it:
      order_service.update_status, payment_service (webhooks + create_session),
      noon_send_service, lalamove_service, pos_order_service (3 string-literal sites).
      Delete the per-module guard sets (_CONFIRMABLE_FROM, courier guards).
- [ ] P0-1: Courier-marked UNDELIVERED now refunds via the same transition machinery
      (closes the refund-promised-but-never-issued gap).
- [ ] P0-2c: Guardrails — attribute-listener validation (loud on illegal transition)
      + AST/grep unit test that no `.status =` assignment on Order exists outside
      order_lifecycle.
- [ ] P0-3: `set_order_state` pairing rules inside transition() for pos_status /
      delivery_status; migration 097 with data cleanup + CHECK constraints for
      impossible combos (e.g. cancelled+active).
- [ ] Verify: pytest unit suite; migration on throwaway Postgres (per lessons.md —
      test suite mocks DB, so migrations must be exercised separately).

## Storefront (apps/web) — delegated to parallel agent, reviewed before commit

- [ ] P0-4: One `usePromoValidation` hook shared by cart + checkout; silent
      re-validation whenever a promo is applied, independent of the fold-out.
- [ ] P0-5a: cart-context — replace closure-snapshot rollback with refreshCart() on error.
- [ ] P0-5b: CartProvider watches `user` (merge on sign-in, clearSessionId+refresh on
      sign-out); delete per-page mergeCart calls; surface merge failure.
- [ ] Verify: vitest + typecheck + lint.

## Review

All P0 items landed, three commits:

- `ae5cecf` — **order_lifecycle** (P0-1/P0-2): `app/services/order_lifecycle.py` is now
  the only module allowed to assign `Order.status`. All 13 write sites routed through
  `transition()`; the five private guard sets deleted; courier UNDELIVERED now auto-refunds
  (the money-losing gap); POS string literals gone; noon's undelivered→out_for_delivery
  redelivery allowance removed (map policy wins — test rewritten to assert refusal).
  Guardrails: AST unit test forbids new direct assignments; runtime listener warns on
  ungated writes. 1364 tests pass.
- `abd5b54`+`…098` — **migration 098** (P0-3): repairs cancelled+open-check rows to
  `void`, then CHECK constraint refuses the combo. Verified on throwaway Postgres 16:
  full 98-migration chain, violating insert rejected, planted bad row repaired.
- web commit — **storefront** (P0-4/P0-5): `usePromoValidation`/`usePromoRevalidation`
  hook shared by cart+checkout, always-on re-validation at checkout (toast on refusal);
  cart-context refreshes from server on mutation error instead of snapshot rollback;
  CartProvider watches auth (merge on sign-in, clearSessionId on sign-out — closes the
  shared-device leak); mergeCart failures surface. 375 tests pass, tsc/lint clean.

Fixed along the way (found during the refactor, kept to the same patterns):
- Cancelling a cashier order from the console no longer restocks stock it never claimed
  (restock now keyed on `source == online`).
- POS `close_order` can no longer walk a packed online order back to `confirmed`.
- POS `void_order` of an attached online order now releases stock and refunds the card
  payment (it used to silently cancel with neither).
- Website orders can no longer be joined into another check (stock/payment/email links
  would silently break).
- Map additions, documented in place: PAYMENT_FAILED→CREATED (retry), CONFIRMED→
  OUT_FOR_DELIVERY and CONFIRMED→DELIVERED (courier collected before packing stamped).

## P1 / P2 / P3 sweep — complete

Everything the audit raised above P3-hygiene is done. In commit order:

- **Shared types generated** (P1-1, P1-5): `@mm/types` is built from the API's OpenAPI
  document; two CI gates (Python-side `export_openapi --check`, Node-side
  `check:fresh`) fail on drift. Dead `@mm/ui` stub and the repudiated
  `packages/config/src/delivery.ts` deleted; both apps carry real `workspace:*` deps.
- **Transactions & email** (P2-2, P2-4): duplicate `get_db` deleted, service commits
  replaced with flushes, device `last_seen_at` moved to its own session; one inline-await
  email policy with an hourly failed-send alarm (monitor, not resend — `email_logs` has
  no payload to replay and no claimable status, so a resender would double-send).
- **Routers** (P2-3, P2-8): `require()` dependency factory replaced five copied helpers;
  zero bare `HTTPException` left in six routers, wire shape proven byte-identical.
- **DB vocabulary** (P1-3, P3-2/3/4/5/8): migrations 099–103 — status CHECKs on 10
  columns, `business_date` format guards, `updated_at` on `order_items`/`promo_codes`,
  `ix_order_items_product_id`, soft-delete consistency on 22 tables; `lazy="selectin"`
  dropped from the four Order collections (audit found every reader already loads them).
- **Admin** (P1-4, P2-5, P2-6): one request layer, `useApiList`/`useDebouncedValue`,
  41 `alert()`/`confirm()` sites replaced with styled dialogs, analytics on
  `allSettled` so one dead endpoint no longer blanks the dashboard.
- **Web** (P1-4, P3-10/12/13/16): `api-client`/`api-server` split behind
  `client-only`/`server-only`, shared `interpolate()`, cache tags/TTLs centralised,
  `formatPrice` adopted, `DEFAULT_ADDRESS_LABEL` unified.
- **Purchase orders & identity** (P2-1, P3-1/6/7/9): PO state machine moved out of the
  router into `inventory_service` mirroring `order_lifecycle`; `phonenumbers` now
  validates server-side (six impossible-number formats newly rejected, every real UAE
  format preserved); `OptionSnapshot` schema validates both wire dialects;
  `reference_integrity` checks UUID-array scoping at the `crud_service` chokepoint.
- **Money** (P1-2, P2-7): `POST /orders/preview` returns the totals the order will be
  written with; checkout renders them instead of re-deriving (the `*5/105` VAT line,
  the mirrored `lowOrderFeeFor` and the two divergent grand totals are gone).
  `page.tsx` 1,621 → 1,090 lines with four hooks and four components extracted.

Found and fixed along the way, not in the audit:
- `alembic downgrade base` had been broken for months: four revisions restored dropped
  columns without the indexes/keys Postgres removed with them, and `024` dropped an
  index it never created. Full up/down/up now verified; `test_migration_chain.py`
  pins the structure (and caught that migrations use two `down_revision` spellings).
- The sixth `_require` copy in `marketing.py` was **dead code** — every route there is
  gated by `build_crud_router`, so it read as protection and enforced nothing.
- Cancelling a counter sale from the console restocked ingredients it never claimed.
- POS `close_order` could walk a packed online order back to `confirmed`.

Still open, deliberately: escalating the ungated-status-write warning to a raise once
the log stays quiet; join tables for the UUID-array columns (service validation landed
instead); `business_date` as a real `Date` (format CHECK landed instead).
