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

Deferred (documented in docs/architecture-audit-2026-08.md): P1 codegen, preview
endpoint, fetch-client consolidation; escalating the ungated-write warning to a raise
once the log stays quiet.
