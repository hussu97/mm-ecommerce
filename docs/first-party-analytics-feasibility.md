# Replacing Umami with our own event log — is it worth it?

**Short answer: yes, it is feasible, and it is smaller than it sounds — because
we already built most of it.** Roughly a week of work for a version that is
genuinely better than what we have, and it removes a dependency that is
currently the reason half our analytics is invisible.

This document is the case, the design, the cost and the honest list of what we
would be giving up.

---

## Why this is even on the table

Three facts about the current setup, in order of how much they matter.

**1. The read API is not in the Umami Cloud free tier.** The storefront can
record events perfectly and the admin dashboard still shows nothing, because
pulling the data back out is a paid feature. This is not a bug we can fix; it is
the shape of the product. Every traffic panel in `apps/admin` depends on it.

**2. We already proxy every event through our own servers.** `next.config.ts`
rewrites `/umami/*` to Umami Cloud so the tracker stays same-origin and off the
privacy blocklists. Which means the traffic already arrives at infrastructure we
control and is then forwarded to somebody else's. Keeping it is a smaller change
than sending it on.

**3. Because of that proxy, Umami cannot see the visitor.** Every event reaches
Umami Cloud from the storefront's server address, so the geography, network and
IP columns in the Umami dashboard describe our hosting provider, not our
customers. We are paying the cost of a proxy and getting none of the data the
proxy is standing in front of. A first-party collector reads
`X-Forwarded-For` and gets the real thing — which is exactly the IP, location,
device, platform and language the question asks for, and which today we
genuinely do not have.

There is a fourth, quieter reason. The interesting questions here are commerce
questions — which category a customer browsed before buying, how many baskets
die at the delivery-fee line, whether Arabic sessions convert differently — and
answering any of them means joining browsing behaviour against the `orders`
table. That join cannot happen while one side lives in another company's
database. It becomes an ordinary SQL query the moment both sides are ours.

---

## What we would build

### The table

One append-only table, in the shape `email_logs` already established here.

```
site_events
  id             uuid pk
  occurred_at    timestamptz   -- server clock, indexed
  name           text          -- 'add_to_cart', 'pageview', …
  url            text          -- path only, query stripped except utm_*
  referrer       text
  visitor_id     text          -- see "Who is this person" below
  session_id     text
  user_id        uuid null fk  -- when signed in. The join that matters.
  order_number   text null     -- stamped on the post-order events
  locale         text          -- 'en' | 'ar'
  -- device, from the User-Agent, parsed once on the way in
  device         text          -- mobile | tablet | desktop
  os             text
  browser        text
  screen         text
  -- place, from the IP, resolved once on the way in
  country        text
  region         text
  city           text
  ip_hash        text          -- see "What we must not store"
  -- the payload the storefront already sends
  data           jsonb
```

Indexes on `(occurred_at)`, `(name, occurred_at)` and `(session_id)` cover every
query the dashboard asks. `data` stays `jsonb` so a new event needs no
migration — the same property that makes the current `analytics.ts` cheap to
extend.

### Getting the events there

The storefront side is nearly free. `apps/web/lib/analytics.ts` is already a
single choke point that every one of the fifteen events goes through; today it
hands off to `window.umami.track`. It would hand off to `navigator.sendBeacon`
against `/api/v1/events` instead — which is *more* reliable than what we have,
because a beacon is the browser's own mechanism for "send this even though the
page is going away", and is precisely the guarantee the checkout events want.

Batching a few events per request and flushing on `pagehide` keeps the request
count near what it is now.

Pageviews would come from the same place: the App Router already knows when it
navigates.

### Getting them back out

The admin analytics page is already built and already draws exactly these
shapes — it just gets its numbers from a proxy to Umami. Those endpoints in
`apps/api/app/api/v1/analytics.py` would query `site_events` instead. The
funnel, revenue and product panels already query our own database, so half the
page needs no change at all.

### Who is this person

Cookieless, the way Umami does it, and for the same reason: it is the version
that needs no consent banner.

```
visitor_id = sha256(daily_salt + ip + user_agent + website_secret)
```

A salt that rotates at midnight means the identifier cannot be reversed and does
not follow anyone past a day. Sessions are the same hash plus a 30-minute idle
window. When someone signs in, `user_id` is stamped alongside — that is the
column the interesting joins hang off, and the one Umami can never give us.

### What we must not store

The raw IP is personal data under the UAE's PDPL and under GDPR for any visitor
in Europe. We would keep the derived facts — country, region, city, and the hash
— and never the address itself. That is not a compliance nicety we are choosing
to accept; it is the difference between an analytics table and a liability, and
it costs nothing, because nothing we want to ask needs the raw value.

Geography from MaxMind's GeoLite2 country/city database: free, self-hosted,
about 60 MB, updated weekly, resolved in-process with no third-party call and
no data leaving our infrastructure.

### Keeping it from growing forever

Raw rows for 90 days, then a nightly roll-up into daily counts per event, per
path, per device, per country, and the raw rows dropped. The dashboard reads
raw for recent windows and the roll-up beyond, which is what keeps a year of
history to a few thousand rows.

At our volume this is precaution rather than necessity. The free tier we are on
caps at 10,000 events a month; even at fifty times that, raw retention is a few
hundred megabytes and Postgres will not notice.

---

## What it costs

| | |
|---|---|
| Table, migration, ingest endpoint, bot filtering, rate limiting | 1 day |
| UA and geo enrichment, visitor/session hashing, the salt rotation | 1 day |
| `analytics.ts` rewritten onto beacons, pageviews, batching | half a day |
| Rewiring the admin analytics endpoints and the panels that change | 1–1.5 days |
| Roll-ups, retention, the purge job | half a day |
| Running both in parallel for a fortnight and reconciling the counts | half a day of attention, spread out |

**About a week**, and it is ordinary work in a stack we already run: one more
table in a Postgres we already have, one more router in a FastAPI we already
have, one more panel in an admin we already built. There is no new
infrastructure, no new service to deploy, no new thing to monitor. The nightly
roll-up is the only new moving part.

Running cost is effectively zero — a table in the database we are already
paying for, against a $9–20/month Umami plan that would otherwise be needed to
read our own numbers back.

---

## What we would be giving up

This is the part worth being honest about.

- **Bot filtering.** Umami maintains this and it is thankless. A user-agent
  blocklist gets most of it; the long tail of headless traffic is real work, and
  ignoring it inflates every number. This is the single biggest hidden cost.
- **The Umami dashboard.** Real-time view, retention curves, session replay of a
  path through the site, the funnel builder. We would be rebuilding the panels we
  actually look at, not all of them, and "we'll add that later" has a way of
  meaning never.
- **Somebody else's correctness.** Sessionisation, timezone handling at day
  boundaries, and bounce definitions are all quietly fiddly, and every one of
  them becomes ours to get wrong.
- **A second opinion.** When our own numbers look strange there is currently a
  independent system to check them against. Afterwards there is not.

---

## Recommendation

**Build it, and keep Umami running alongside for a fortnight rather than
switching.**

The deciding argument is not cost and it is not control. It is that the
questions this shop actually needs answered — which browsing behaviour turns
into orders, whether Arabic customers convert differently, where in the checkout
the baskets die — are all joins between behaviour and the `orders` table, and
that join is impossible while half the data sits in another company's database
behind a paid API. We are not replacing an analytics tool with a worse copy of
it; we are moving the behavioural half of a question next to the commercial half
that is already here.

The fixes shipped alongside this document mean nothing needs to be decided under
pressure. Events are no longer being sent twice, no longer dropped after three
seconds, and the dashboard now reads them back and says so when Umami refuses.
Whatever the current numbers turn out to be, they are now numbers we can trust,
and a fortnight of running both would say plainly whether a first-party log
agrees with them.

**If we do not build it**, the minimum is an Umami Cloud plan with API access.
The tracking is fine. Being unable to read it back is what has made it look
broken.
