# Should the shop send a cart-abandonment email?

**Date:** 2026-08-20
**Status:** assessment — a recommendation, not a build. Nothing in this document
is wired up; the data it depends on is.

---

## The short answer

**Yes, and the groundwork is now done — but do not write the sending code until
the Live Baskets screen has run for a week.** The one number that decides
whether this is worth building is `reachable_value` on
`/analytics/carts`: the goods value of idle baskets that carry an email address.
Until this change there was no way to read it, and every estimate of what
abandonment recovery is worth here was a guess borrowed from someone else's shop.

Give it seven days, filter to *Idle 1+ hour*, and read the header. The decision
falls out of it:

| What the header says | What to do |
|---|---|
| Reachable value is a meaningful share of a week's takings | Build the sweep. Two touches, as sketched below. |
| Plenty of live baskets, almost none reachable | The problem is **capture**, not sending. See "The reachability ceiling". |
| Very few idle baskets at all | Nothing to recover. Spend the effort on the next item in `tasks/conversion-audit.md`. |

---

## What changed, and why it had to

`tasks/conversion-audit.md` puts abandoned-cart recovery at the top of Tier 1 and
names the blocker exactly:

> A guest cart has no email address on it. Email is collected at checkout but
> never written back to the cart, so a basket abandoned *before* the checkout
> form is unreachable, and one abandoned *during* checkout is reachable but
> nothing stores the link.

Two columns close that (migration `116`):

- **`carts.guest_email`** — the address typed into the checkout form, written
  back by `POST /orders/preview`, which was already asking for it in order to
  judge a new-customer coupon and then discarding it. Guest baskets only: an
  account basket already has an address on `users.email`, and a second copy here
  would be free to go stale.
- **`carts.last_activity_at`** — when the shopper last touched the basket.
  `updated_at` could not answer that, because adding a line writes `cart_items`
  and never touches the `carts` row at all: a basket actively filled for ten
  minutes could carry an `updated_at` from the moment it was created. Stamped by
  `cart_service.touch` on every basket read and write, throttled to a minute so
  an ordinary browsing session is not one row update per page view.

On top of them, `GET /analytics/live-carts` and the **Live Baskets** screen.
That screen is worth having whether or not the email is ever built — "eleven
people are holding four thousand dirhams of cake right now, and six of them
stopped two hours ago" is an operational fact the shop has never been able to
see.

---

## The reachability ceiling

This is the number that governs everything, and it is worth being blunt about:
**a basket is only reachable if the shopper reached the checkout form.** Someone
who added a cake, browsed on and closed the tab, with no account, leaves a row
with a session id and nothing else. No email exists for them anywhere, and none
can be conjured.

So the recovery rate is not "5–15% of abandoned baskets" — the industry figure
quoted in the conversion audit. It is 5–15% **of the reachable slice**. The
screen shows both, side by side, which is precisely why the header carries
`with_email` next to `carts`.

If the reachable slice turns out to be small, the fix is upstream of the email
and is a separate decision with its own trade-offs — capturing an address
earlier (on the cart page, beside the free-delivery nudge) buys reach at the cost
of friction on the highest-intent screen on the site. Do not fold that into this
build; measure first.

---

## If it is built: the shape

### Where it runs

`batch_scheduler.sweep_once` already ticks every 60 seconds under an advisory
lock, and already carries a fifth sweep whose comment explains the pattern:
*"an abandoned basket is two days old by the time it qualifies, so which minute
of the day it is noticed in does not matter to anybody."* A sixth sweep belongs
beside it, wrapped in the same never-at-the-batches'-expense `try/except`. It
needs no new loop, no new lock, and no cron this stack does not have.

### What it sends to

```
carts holding items
  AND last_activity_at between (now - 25h) and (now - 1h)
  AND an address: users.email via user_id, or guest_email
  AND no email_logs row for this cart at this stage
```

`EmailLog` is the dedup, as it is everywhere else — the journal is the truth
(`email_service`, module docstring). Give the abandonment templates their own
`template` values (`cart_reminder_1h`, `cart_reminder_24h`) and carry the cart id
so "has this basket already been chased" is one indexed lookup, not a guess.

### Timing

Two touches, 1 hour and 24 hours. The first is the one that works: for food
delivery, abandonment is usually an interruption rather than a rejection, and an
hour later is the same craving. The second catches the next evening. A third is
where these sequences start reading as harassment, and this shop sells to a small
city where its reputation is one thing.

### When it must stop

Every one of these is a real way to send an embarrassing email, and every one of
them is cheap to check:

- **They ordered.** The basket is cleared at checkout, so an emptied basket
  drops out of the query on its own — but check for an order placed by that
  identity since `last_activity_at` as well, because a guest who ordered from a
  second device leaves the first basket standing.
- **They came back.** `last_activity_at` moves and the basket leaves the 1-hour
  window. This is exactly why the column exists and why it is not `updated_at`.
- **The basket is empty.** Already excluded.
- **They asked not to be written to.** See below.
- **The items are gone.** A basket holding a product that has since been hidden
  or sold out should not be advertised back to anybody. `availability_service`
  answers this and the checkout already asks it.

### Consent, and the unsubscribe

This is a **marketing** email, not a transactional one, and that distinction is
the whole compliance question. The address was given in the course of a
transaction the customer started, which is the ordinary soft-opt-in basis
recognised in most regimes — but that basis comes with conditions, and the two
that matter are:

1. **A one-click unsubscribe in every send**, honoured immediately and stored in
   a suppression list the sweep consults *before* it selects, not after.
2. **A record of when and where the address was given.** `carts.created_at` and
   `last_activity_at` provide it for guest addresses; account addresses have
   their own registration record.

There is no suppression list in this codebase today. It has to be built with the
sweep, not after it — retro-fitting one is how a shop ends up mailing somebody
who asked twice to be left alone.

The UAE's PDPL (Federal Decree-Law 45 of 2021) and the TDRA's rules on unsolicited
electronic marketing both bear on this, and so does GDPR for any EU customer who
has ever ordered. **This document is not legal advice and the shop should put the
final wording and the consent basis past its own counsel before the first send.**
The engineering point stands regardless: build the unsubscribe and the suppression
list as part of the feature, because no legal answer makes them optional.

### Deliverability

Order confirmations and password resets go out on the same Resend domain. A
marketing sequence generates complaints in a way transactional mail does not, and
a complaint rate high enough to hurt that domain's reputation would start
landing **order confirmations** in spam — a far worse outcome than never sending
a reminder at all. Send this from a subdomain, keep the transactional stream
separate, and watch the bounce and complaint rates for the first month.

### Cost

Resend per-email, and nothing else. No new infrastructure, no vendor, no manual
effort after launch. If a new setting is added (a feature flag, a subdomain
sender), it goes in **all five** places in the CLAUDE.md §9 checklist — item 5,
the `environment:` allow-list in `docker-compose.prod.yml`, is the one that gets
forgotten, and `tests/unit/test_compose_env_allowlist.py` now fails if it is.

---

## What was deliberately not built

- **The email itself.** The ask was to assess it. The assessment says measure
  first, and a sweep written before the measurement is a sweep written before
  anybody knows whether it has anything to send.
- **A discount in the reminder.** The obvious next thought, and the expensive
  one: a shop that reliably emails 10% off an hour after you leave has taught its
  customers to leave. If the plain reminder converts, that is margin kept. Test
  the plain one first.
- **A discount figure on the Live Baskets screen.** The basket stores the promo
  *code*, never what it is worth — what a coupon is worth depends on the basket,
  the identity and the day, and it is decided at the checkout every time it is
  asked (migration `115`). `estimated_total` on that screen says "before promo"
  rather than quoting a second answer the checkout would then contradict.
- **A CSV export of reachable baskets.** Tempting as a way to send the first
  round by hand. Left out on purpose: a manual send has no unsubscribe, no
  suppression list and no journal, which is the whole compliance surface above,
  skipped.

---

## Open decisions for the shop

1. Plain reminder, or reminder with an offer? (Recommendation: plain, first.)
2. One touch or two? (Recommendation: two — 1h and 24h.)
3. Capture an email earlier on the cart page to raise the reachable slice, and
   accept the friction? (Recommendation: only if the week of data says the
   ceiling is the binding constraint.)
4. Which sending domain, and who watches the complaint rate?
