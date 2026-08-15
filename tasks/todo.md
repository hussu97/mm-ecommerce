# The courier is called when the order is accepted, not when it is packed

## Why

`MM-20260815-001` was packed at 11:21 on 15 Aug, sat with `batch_id` NULL and
`last_error = "Courier is not configured; dispatch this order by hand"`, and
nothing in the system was ever going to pick it up again. Two separate faults
met on that one order, and both are fixed here.

**The first was a missing credential.** The order was packed from the register,
and the `pos-api` container had no `LALAMOVE_*` variables — item 5 of the secret
checklist, for the second time. `is_enabled()` was False, so
`assign_or_dispatch` returned at its first guard, never looked at the zone's
batch group, and fell through to the single-order path. Already fixed by
`3657754` and deployed; not part of this change.

**The second is that nothing retries a single dispatch.** `dispatch_due_batches`
sweeps *batches*. An order that failed on the un-batched path is in no batch, so
no sweep will ever see it. `assign_or_dispatch` runs on the `packed` transition
and that transition has already happened. The order waits for a human to notice
a red box on an admin screen — which is exactly what happened, for six hours.

And behind both: **`packed` is the wrong trigger.** It is a person pressing a
button to say something the system could work out, and until they press it no
driver has been called. A kitchen with an iPad nobody is watching is a kitchen
where every order waits for someone to walk over twice — once to accept, once to
say it is boxed. The prep time is long enough to call the driver at the start of
it, so that is where the call moves.

## Decisions taken (asked and answered before writing this)

| Question | Answer |
|---|---|
| Driver timing | **Book immediately on accept.** No scheduled orders, no prep-time setting. |
| Batch cutoff | **Always join the window covering accept-time.** No ready-by check. |
| Auto-accept scope | **Stays per terminal** (`devices.auto_accept_online_orders`). |
| `packed` | **Auto-stamped, button dropped.** Status and tracker unchanged. |

## Design

### 1. Retry, with the ladder that already exists

`delivery_batches` carries `attempt_count` / `next_attempt_at` / `last_error`
and `RETRY_BACKOFF = (5m, 15m, 45m)`. `order_deliveries` carries only
`last_error`. It gets the other two, with the same names and the same meaning,
because a second vocabulary for "when do we try again" is how the two paths come
to disagree about it.

* Migration `094` adds `dispatch_attempts INT NOT NULL DEFAULT 0` and
  `next_attempt_at TIMESTAMPTZ NULL` to `order_deliveries`, indexed on
  `next_attempt_at` — the sweep's only predicate.
* `courier_service.dispatch` becomes the one place that records the outcome. It
  already funnels both providers and the fallback; the provider modules keep
  writing `last_error` and stay ignorant of retries.
* A failure schedules the next rung and, on the last one, stops and leaves the
  row for a human — the same two ending conditions as `_retry_at`, including
  the kitchen-hours one. A booking made at 00:05 sends a driver to a dark shop.
* A success clears `last_error`, `next_attempt_at` and the counter.
* `batching_service.retry_failed_dispatches` selects deliveries that are due,
  `FOR UPDATE SKIP LOCKED`, and re-enters `assign_or_dispatch` — not
  `courier_service.dispatch`. Re-entering at the top means an order that failed
  while the courier was misconfigured can *join a batch* once it is configured,
  rather than being condemned to the single-order path by its first failure.
* `batch_scheduler.sweep_once` calls it inside the advisory lock it already
  holds. One sweep, one lock, two kinds of work.

**"Not configured" retries like anything else.** It is the failure most likely
to be fixed by a deploy twenty minutes later, and this whole document exists
because it was not retried.

### 2. Acceptance is the trigger

`POST /pos/orders/{id}/accept` calls `batching_service.assign_or_dispatch` after
flipping `pos_status` to `active`. That single call already contains every
branch this needs:

* **Third-party zone** → `books_itself` is False, returns untouched. There is no
  driver to call, so acceptance does nothing but put the order on the register
  and the paper. This is the "send it right away" case.
* **Integrated zone, no batch group** → books a driver now.
* **Integrated zone, in a group** → joins the run whose window covers *now*.

`dispatchable_at` now means *accepted*, not *packed*. The column keeps its name
and its job — the moment a window is matched against — and its docstring is
rewritten rather than the column renamed, because a rename buys nothing and
breaks `reschedule_group`.

**`packed` stays wired as a backstop, not removed.** Not every order is accepted
on a register: a branch with no terminal receiving online orders has no
acceptance event at all, and an admin marking such an order packed must still
call a driver. `assign_or_dispatch` is idempotent — it returns early on an order
already batched or already booked — so both triggers can coexist and the second
one is free.

### 3. `packed` stamps itself

Nobody presses it any more, so something has to.

* **Integrated couriers** — stamped when the booking succeeds. A driver has been
  called for this box; the customer's "on its way" email is the honest thing to
  send, and `PICKED_UP` later moves it to `out_for_delivery`. The webhook guard
  already accepts that transition from `confirmed` *or* `packed`, so an order
  that never passed through `packed` is not stranded either way.
* **Third-party zones** — stamped when the last kitchen ticket for the order is
  completed. That is the shop physically finishing the box, which is what the
  word meant when a person was pressing it.
* `POST /pos/orders/{id}/packed` stays and keeps working. It is what a register
  in the wild will call until every iPad has updated, and removing an endpoint
  that live devices still hold is how a shop loses a day.

The POS **button** goes. The endpoint does not.

### 4. The alarm rings for five minutes, including on auto-accept

Today the alarm is suppressed entirely on an auto-accepting terminal, on the
grounds that an alarm exists to fetch a person and there is nothing for them to
do. That was wrong in the one way that matters: the kitchen still has to *make*
the cake, and a receipt sliding silently out of a printer nobody is next to is
an order nobody starts.

* Auto-accept no longer suppresses the tone.
* A raised alarm has a floor of **five minutes**. Below that it cannot be
  silenced by anything — an auto-accepted order has no Accept button to press,
  so a silence that needs a person defeats the point.
* After the floor: an auto-accepted order's alarm stops by itself; a manual one
  keeps ringing until somebody accepts it, exactly as now.
* **iPad only**, by explicit instruction. This breaks the consistency rule in
  `mm-pos/CLAUDE.md`, so it breaks it *once*, in the shared kit, behind a named
  `OrderAlert.isLoudTerminal` gate with the reason written next to it — rather
  than by putting an `if` in `MMPosPad/`.

### 5. Printing while backgrounded (iPad only)

The register is a shared iPad and it will not always be the frontmost app.
Printing today needs the app awake, which means an order accepted while somebody
is in Safari prints when they come back to it.

* `MMPosPad` gets a **real `Info.plist`** with
  `UIBackgroundModes = [remote-notification, audio]`. `GENERATE_INFOPLIST_FILE`
  is turned off for that target only. The comment in `PushService` records that
  `INFOPLIST_KEY_UIBackgroundModes` was tried and silently does nothing — this
  is the fix it points at.
* `push_service._alert` gains `"content-available": 1` on the order-placed push,
  so iOS wakes the app to run the handler as well as showing the banner.
* `PushAppDelegate` implements
  `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)`, which
  fetches the branch's pending orders, auto-accepts and prints them if the
  terminal is set to, and calls the completion handler. ~30 seconds of wall
  clock, which a LAN ESC/POS write fits inside comfortably.
* The `audio` mode is what lets the tone keep sounding once the app is not
  frontmost. Without it iOS stops the session the moment it backgrounds and the
  five-minute floor means nothing.

### 6. One number on the paper

Three lines print an identifier today and two of them are labelled things
nobody asked for: `External Number:` on the receipt and the kitchen ticket, and
`Courier ref:` in the delivery block.

They collapse to **one line, labelled `Order number:`**, whose value is the
first of `courierReference`, `externalReference`, `orderNumber` that exists —
and which is **omitted entirely when the value is the order number already
printed in the box at the top**. So:

| Order | Prints |
|---|---|
| Lalamove / noon Send | `Order number: 4821907` (the reference the driver quotes) |
| Talabat / Deliveroo | `Order number: 3825713004` (theirs) |
| Third-party courier | nothing extra — the boxed `Order# MM-…` is the only number |

## Work

- [ ] `094_delivery_dispatch_retry.py` — two columns, one index
- [ ] `OrderDelivery.dispatch_attempts`, `.next_attempt_at`, `.is_awaiting_retry`
- [ ] `courier_service.dispatch` records outcome and schedules the ladder
- [ ] `batching_service.retry_failed_dispatches` + `_dispatch_retry_at`
- [ ] `batch_scheduler.sweep_once` calls it under the existing lock
- [ ] `accept_order` calls `assign_or_dispatch`
- [ ] `dispatchable_at` docstring; `assign_or_dispatch` docstring
- [ ] auto-stamp `packed` on booking success (integrated)
- [ ] auto-stamp `packed` on last kitchen ticket completed (third party)
- [ ] admin order screen: show retry state, not just `last_error`
- [ ] `OrderAlert`: five-minute floor, `isLoudTerminal`, auto-accept rings
- [ ] `IncomingOrdersModel`: drop the `if !autoAccept` suppression
- [ ] drop "Mark packed" from both apps' incoming-orders UI
- [ ] `MMPosPad/Info.plist` + background modes + `content-available`
- [ ] `PushAppDelegate` background fetch-and-print
- [ ] `ReceiptRenderer`: one `Order number:` line, receipt and kitchen ticket
- [ ] tests: retry ladder, accept-triggers-dispatch, receipt line, alarm floor
- [ ] `swift test`; both app targets build

## Review

_(filled in when the work lands)_
