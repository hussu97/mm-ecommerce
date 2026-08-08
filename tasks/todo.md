# Unpaid orders were reaching the POS registers

## Why

Orders sitting at `status = created` were appearing on the iPad and iPhone POS
apps' incoming list, sounding the unaccepted-order alarm and offering a cashier
an **Accept** button — for a sale nobody had paid for.

`order_service.create_order` put every storefront order on a branch's register
one line after the insert: `attach_online_order` stamped `is_pos`, a check
number, a business day and `pos_status = pending`, then `notify_order_placed`
pushed every terminal at the branch. That runs at checkout. A card order is
`created` at that point and stays there until Stripe's
`payment_intent.succeeded` — and for an abandoned checkout, a card never
entered, or an ignored 3-D Secure prompt, it stays `created` forever.

Cash and zero-total orders confirm at creation and are genuinely work. Every
other payment method has an event to wait for, and we were not waiting for it.

## Plan

Two halves, because a write-path fix cannot reach the rows already wrong in
production.

### 1. Publish on confirmation, not on insert
- [x] `order_service.publish_to_register()` — attach + push in one place, keyed
      on the order being *confirmed*, no-op for a counter order, idempotent
      (the push guards on the same check number the attach does)
- [x] `create_order` calls it only when the order confirmed at checkout (cash)
- [x] `payment_service._handle_payment_succeeded` — the card path
- [x] `payment_service.create_session` — zero-total and cash-on-collection
- [x] `order_service.update_status` — an admin confirming by hand

### 2. Never *read* an unpaid storefront order onto a register
- [x] `pos_order_service.is_paid_for()` / `paid_for_clause()` — one rule, in
      Python and in SQL
- [x] Applied to `GET /pos/orders`, `GET /pos/orders/dispatch/board`,
      `GET /pos/kitchen/open-checks`
- [x] `POST /pos/orders/{id}/accept` returns 409 for an unpaid order, so a
      device holding a stale row or replaying a queued action cannot pull one
      onto a check

### 3. Tests
- [x] `test_unpaid_orders_stay_off_the_register.py` — the rule over every
      `OrderStatusEnum` member, both channels, the SQL mirror, publishing and
      its idempotency, and each confirmation route
- [x] `test_order_service.py::TestOnlyPaidOrdersReachTheRegister` — a card
      order is not published at checkout; a cash order is

## Review

`1108 passed, 21 skipped`. `ruff check` and `ruff format` clean.

**No iOS change, so no TestFlight release.** The incoming queue is entirely
server-driven: `IncomingOrdersModel` holds no cache, polls `/pos/orders` every
20 seconds, and `refresh()` already acknowledges the alarm for any order that
has left the waiting list. The registers clear themselves within one poll of
the API deploy.

**No migration.** The storefront orders already sitting `is_pos = true,
pos_status = pending, status = created` in production stay in the table and
simply stop being returned. If one of them is ever paid for, the read filter
lets it through and it appears on the counter correctly — which is the right
behaviour, and a `DELETE` or a `void` sweep would have thrown that away.
