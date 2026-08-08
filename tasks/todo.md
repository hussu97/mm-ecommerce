# Multi-gateway card payments — Stripe **or** Ziina

## The problem

"Stripe" is currently three different things wearing one word: the payment
*method* the customer picks, the *gateway* that processes it, and the shape of
every webhook, column and code path that touches money. So there is no seam to
switch on. When Stripe has an incident — and it will — the only lever is a
deploy.

The customer never had an opinion about this. They picked **card**. Which
processor carries it is an operations decision, and it should be an operations
*switch*.

## The split

| Concept | Values | Who decides | Where it lives |
|---|---|---|---|
| Payment **method** | `card`, `cod` | the customer | `orders.payment_method` |
| Payment **gateway** | `stripe`, `ziina` | us, at runtime | `orders.payment_provider` |

`payment_provider` already holds exactly this (`'stripe'`/`'cod'`) — it keeps its
name and its meaning gets written down, rather than renaming a column on a live
table for a word.

## Plan

### Database (migration `089`)
- [x] `payment_gateways` — the routing table, modelled on `couriers`: `code`,
      `name`, `is_active`, `priority`, `supports_failover`, `min_amount`,
      `max_amount`, `test_mode`. Seeded **stripe active / ziina inactive**.
- [x] `payment_transactions` — one row per attempt against a gateway:
      `gateway`, `session_id`, `payment_id`, `status`, `amount`, `checkout_url`,
      `error_*`, `raw_status`. This is what replaces sniffing `pi_` off a
      string to decide whether money moved.
- [x] Backfill `orders.payment_method`: `'stripe' → 'card'`.

### Provider interface
- [x] `providers/base.py` — `PaymentGatewayProvider` returning normalised
      `GatewaySession` / `GatewayEvent`; `PaymentEventType` enum so
      `payment_service` never sees a gateway's own vocabulary.
- [x] `providers/stripe_provider.py` — adapted, behaviour unchanged.
- [x] `providers/ziina_provider.py` — new. `POST /payment_intent`, amount in
      fils, `X-Hmac-Signature` (hex SHA-256 HMAC) on the way back.
- [x] `providers/tabby_provider.py`, `tamara_provider.py` — moved onto the new
      base so they stay honest stubs.

### Routing
- [x] `payment_gateway_router.py` — picks the highest-priority gateway that is
      active, configured, and in range; `failover_after()` for the next one.
- [x] `payment_service.create_session` takes a **method**, not a gateway, and
      fails over on a transport error.
- [x] `payment_service.handle_webhook(gateway, …)` — one code path for every
      gateway. Order resolution: metadata → transaction → legacy `payment_id`.

### API
- [x] `POST /payments/create-session` accepts `method` (`provider` still
      accepted — prod is live and rolls out in two pieces).
- [x] `POST /payments/webhooks/{gateway}` generic, with `/webhooks/stripe` and
      `/webhooks/ziina` kept as named routes.
- [x] `GET|PATCH /payment-gateways` for the admin — the actual switch.

### Frontend
- [x] web: `'stripe' | 'cod'` → `'card' | 'cod'`; legacy values normalised on
      read so a retry of an old order still works.
- [x] admin: gateway shown on the order; a Payment Gateways screen to flip it.

### Production must stay Stripe-only
- [x] `ziina` seeds **inactive**.
- [x] `ZIINA_ENABLED` defaults `false`; `is_configured()` is false without it.
- [x] The admin refuses to activate a gateway that is not configured — so the
      button exists in prod and cannot do anything.
- [x] Test pinning all three.

### Checklist rule 9 — the five places
- [x] `apps/api/.env.example`
- [x] `PRODUCTION.md` step 13c
- [x] `.github/workflows/deploy.yml`
- [x] `.github/workflows/rollback.yml`
- [x] `docker-compose.prod.yml` — the one that is silent when forgotten

### Rule 10 — analytics
- [x] `payment_method_selected` now reports `card` where it reported `stripe`;
      `docs/umami-analytics-setup.md` updated with a changelog row.

## Follow-up: the audit log, and the analytics split

Two things the gateway work implied and did not do.

### Payment webhooks join `webhook_logs` (migration `090`)
- [x] `courier_order_id` → `external_id`. The column means "their id for
      whatever this push is about", and writing `pi_3RxK…` into something called
      `courier_order_id` is the same conflation the gateway split had just
      finished removing from `orders`. `order_deliveries` and `delivery_batches`
      keep theirs — those genuinely are courier bookings.
- [x] Every payment webhook recorded, on the recorder's own session, **whatever
      it does**. The rows worth having are the ones a success-only log would not
      contain: the forged signature, the event that matched no order, the
      handler that raised. That last one is the exact shape of the three-day
      outage — the failures left no record and stdout had been taken by a
      restart.
- [x] `endpoint` records which *mount* answered (`payments` / `webhooks`), so
      "which URL is Stripe actually configured against" is answerable from the
      admin instead of by asking Stripe.
- [x] `matched` stays **null** when no lookup happened — a duplicate, a
      payment-in-progress transition. Only `false` means "we should have found
      an order and did not", which is what keeps that column worth watching.
- [x] `signature_valid = false` is reserved for authentication failures, not
      every unparseable body. Diluting it makes it useless as an alert.
- [x] Admin screen: Stripe and Ziina in the filters, both mounts, generic
      column labels.

### The revenue breakdown stops answering two questions on one axis
- [x] `by_payment_method` (`card` / `cod`) — the commercial split, stable no
      matter which processor carries the cards this week.
- [x] `by_payment_gateway` (`stripe` / `ziina`) — card only. Cash is excluded
      rather than shown as a third slice; "cod" is not a gateway.
- [x] `by_payment_provider` kept as an alias of the method split so nothing
      built against it breaks mid-deploy.
- [x] Legacy `stripe` values in `payment_method` normalised **and merged** on
      read — un-merged they draw a phantom third slice that shrinks as old
      orders age out, which looks exactly like a real trend.
- [x] Cache key bumped to `v2`; the old shape would fail validation on read.
- [x] The gateway chart hides itself until a second processor has traffic. One
      full-width bar labelled "stripe" is not a breakdown.

## Review

**Verified, not assumed.**

- API: 1164 passed / 21 skipped. 80 of those are new and about this change.
- Web: 267 passed, `tsc --noEmit` clean over 2068 files. Admin: `tsc` clean.
- `ruff check` and `ruff format --check`: clean across all 384 files.
- The migration was run against a **throwaway Postgres 16**, from empty to head:
  the whole chain applies, `payment_gateways` seeds `stripe` active / `ziina`
  inactive, and the backfill was checked against three planted
  production-shaped rows. `stripe → card` on the method; `cod` untouched;
  `payment_provider` untouched. `downgrade` then `upgrade` round-trips and
  restores the previous release's vocabulary, so a rollback leaves a database
  the running code can read.
- The API was **booted against that migrated database** and the live endpoints
  answered correctly: `/payment-gateways` 401 unauthenticated, the Ziina webhook
  400 (refused, not a polite 200), an unknown gateway 404.
- The production lock was exercised against the real database, in three steps:
  with no credentials the router picks `stripe`; **with the Ziina row switched
  on and still no credentials it still picks `stripe`**; only with the flag *and*
  a key does it pick `ziina`. An AED 1.50 basket is refused with a sentence
  naming the AED 2.00 floor.

**One real bug, found by running it.** The gateway seed bound `min_amount` as a
string, and asyncpg binds `str` as `VARCHAR`, which Postgres refuses to compare
to `numeric`. It would have failed on the very first migration run in any
environment. Every test passed while it was broken — nothing but an actual
database was ever going to catch it.

**Deliberately not done:** Ziina is not launched. No production secret is set,
the row ships inactive, `ZIINA_ENABLED` is false in three separate files, and
the admin refuses to activate a gateway it has no credentials for. The
instructions for turning it on later are in `PRODUCTION.md`, in order.

**Deferred, and worth doing:** an admin view of `payment_transactions` per order
(the data is captured and recorded from today; nothing renders the history yet),
and a reconciliation job driving `ziina_provider.fetch_payment_intent` for
intents whose webhooks were missed — Ziina retries three times and then stops,
unlike Stripe's three days. Neither blocks this, and neither matters while
Ziina takes no traffic.
