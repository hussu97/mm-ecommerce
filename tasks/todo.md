# Payment-failure reason: deep-dive the gateway signal, tell the customer why

Orders MM-20260824-002 / -003 failed at Stripe. Today we keep only `code` +
`message` from Stripe and the customer always sees the same generic
"Payment was cancelled" toast on return to checkout. `OrderResponse` exposes no
failure reason at all, so the frontend *cannot* do better.

## Findings (grounded in the docs)

- Stripe `payment_intent.payment_failed` → `last_payment_error` carries
  `decline_code` (the real reason: `insufficient_funds`, `expired_card`,
  `incorrect_cvc`, …) which we currently throw away. `code` is usually just
  `card_declined`. Full table: https://docs.stripe.com/declines/codes
- Ziina's `latest_error` is only `{message, code}` where `code` is an HTTP
  status — **no decline taxonomy**. So Ziina → pass its human `message` through
  verbatim (localization not required); Stripe → normalized reason code +
  localized copy. (Ziina is off in prod behind `ZIINA_ENABLED`.)

## Plan

Backend
- [ ] `providers/base.py`: `PaymentFailureReason` enum + `GatewayEvent.failure_reason`
- [ ] `stripe_provider.py`: capture `decline_code`, map code/decline_code → reason
- [ ] `ziina_provider.py`: leave `failure_reason=None`, keep message passthrough
- [ ] `payment_transaction.py`: add `failure_reason` column (+ CHECK) & migration
- [ ] `payment_service._record_transaction`: persist `failure_reason`
- [ ] `schemas/order.py`: `payment_failure_reason` + `payment_failure_message`
- [ ] `order_service`: eager-load transactions; populate the two fields (guarded)
- [ ] email: `failed.reason.*` copy (en/ar) + render in `payment_failed.html`

Frontend
- [ ] web `lib/types.ts` + `seed_i18n.py` `checkout.payment_failure.*` (en/ar)
- [ ] `useRetryOrder.ts`: reason code → localized toast; else raw message; else generic
- [ ] admin: show reason + raw code/message on failed orders
- [ ] regenerate `packages/types`

Verify
- [x] unit tests for the Stripe reason mapping + to_response population

## Review (done 2026-08-25)

Shipped all three layers + admin + email, all green.

- `providers/base.py` re-exports `PaymentFailureReason` (defined on the model, so
  a model never imports a service); `GatewayEvent.failure_reason` added.
- `stripe_provider.py`: captures `decline_code`, stores the *granular* code in
  `error_code`, normalises code/decline_code → reason via `_FAILURE_REASONS`
  (full Stripe table, grouped by customer action; fraud/lost/stolen masked to
  `card_declined` per Stripe policy; unknown → `card_declined`).
- `ziina_provider.py`: `failure_reason=None` — no taxonomy; message passed
  through verbatim (per your call).
- `payment_transactions.failure_reason` column + CHECK; migration `148`.
- `order_service._apply_payment_failure`: only while `payment_failed`, latest
  failed attempt, guarded against unloaded transactions; `payment_transactions`
  added to `_order_load_options`.
- `OrderResponse.payment_failure_reason` + `payment_failure_message`; types
  regenerated; hand-written web/admin `Order` updated.
- Toast: `useRetryOrder` prefers reason code → localised, else raw message,
  else generic. i18n keys en+ar in `seed_i18n.py`.
- Email: `failed.reason.*` copy en+ar, rendered in `payment_failed.html`.
- Admin order page shows the decline reason + raw message on failed orders.

Tests: `test_stripe_webhook_parsing` (+4), `test_payment_failure_reason` (new,
4), full payment/order/email surface 737 passed; web+admin tsc clean; single
alembic head.

Known limits (by design):
- Race: customer often lands back before the `payment_failed` webhook is
  processed → order still `created`, no reason yet → generic toast. Acceptable;
  the email carries the reason regardless. A short delayed re-fetch could close
  it later if desired.
- Past failures have no `failure_reason` (no backfill — the raw code's context
  is gone). New failures fill it going forward, so MM-20260824-002/-003
  themselves won't show a reason retroactively.
- Analytics untouched (avoids the W10 umami-doc cascade); `paymentCancelled`
  still fires on return.
