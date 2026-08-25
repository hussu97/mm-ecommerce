# Apple Pay on checkout — test user only

## Goal
For the test user **h_abbasi97@hotmail.com** only (logged in, not guest), show an
in-page **Apple Pay** payment option on checkout — but only when (a) the active
default card gateway is **Stripe** (not Ziina), and (b) the browser actually
supports Apple Pay. When selected and the checkout is otherwise ready, replace
the "Place Order" button with an Apple-styled Apple Pay button that takes the
payment in-page via Stripe (no redirect to the hosted Stripe page).

Guests and every other user must never see it.

## Design decisions
- Apple Pay is a **card wallet**, so the order is still created with
  `payment_method = card` (wire `stripe`). Apple Pay is a *checkout-page-local*
  selection, NOT a new value in the shared `PaymentMethod` type or the wire
  contract. Keeps the provider-agnostic abstraction intact.
- In-page flow = Stripe **PaymentIntent** + Payment Request API. A PaymentIntent
  carrying `metadata.order_number` reconciles through the **existing**
  `payment_intent.succeeded` webhook path — the same path production card
  payments already use. No new webhook/lifecycle code.
- Do **not** set `order.payment_id = pi_...` at intent creation (would trip the
  `_is_paid` legacy-prefix check). Record the intent id only on the
  `PaymentTransaction` row; the webhook sets it on the order when it succeeds.
- Server enforces the test-user gate on both new endpoints (defense in depth).

## Backend (apps/api)
- [x] `app/services/payments/apple_pay_service.py` — test-email allowlist,
      `is_test_user`, `eligibility(db, amount)` (stripe is top candidate),
      `create_intent(db, order_number, user)`.
- [x] `stripe_provider.create_payment_intent(order, *, idempotency_key)`.
- [x] Two endpoints in `app/api/v1/payments.py` (mounted at `/payments`):
      `GET /payments/apple-pay/eligibility`, `POST /payments/apple-pay/intent`.
- [x] Regenerate `packages/types/openapi.json` + `@mm/types` generated.ts.
- [x] pytest: test-user gate, eligibility reflects gateway, endpoint auth.

## Frontend (apps/web)
- [x] Add `@stripe/stripe-js` dep + lockfile.
- [x] `lib/api-client.ts` — `applePayEligibility`, `createApplePayIntent`.
- [x] `hooks/useApplePay.ts` — Stripe.js load, eligibility, canMakePayment,
      `pay()` (create order → intent → confirm → confirmation).
- [x] `page.tsx` — show Apple Pay option row (test user + available); when
      selected + gate ready, render Apple-styled Apple Pay button.
- [x] Pure helper + unit test for the "show apple pay option" gate.

## Verify
- [x] `ruff check/format`, `pytest` (2358 passed), OpenAPI `--check`.
- [x] web `lint` (0 errors), `test` (518 passed), `tsc --noEmit`, `build`, `@mm/types check:fresh`.
- [ ] Commit, push, PR, drive CI green, merge, deploy green.

## Review
Backend and frontend implemented and locally green. All backend boxes and the
frontend boxes are done (marked above). Apple Pay is a checkout-page-local
selection over an unchanged `card` order; the money settles through the
existing `payment_intent.succeeded` webhook. The feature is invisible to guests
and every non-allowlisted account, and hides itself on any browser/gateway that
cannot take Apple Pay. Operational follow-up for the owner: verify the domain
with Stripe/Apple so `canMakePayment()` lights up in production.

## Notes
- Apple Pay only renders when the domain is verified with Stripe/Apple; until
  then `canMakePayment()` returns null and the option stays hidden — which is
  exactly the required "hide on unsupported browsers" behavior. Domain
  verification in the Stripe dashboard is an operational step for the owner.
