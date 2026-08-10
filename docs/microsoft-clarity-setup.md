# Microsoft Clarity Setup — Melting Moments

This file tracks every manual configuration that must exist in the Clarity
dashboard, and the reasoning behind the choices made in code.

Clarity is the companion to Umami, not a replacement for it:

| | Umami | Clarity |
|---|---|---|
| Answers | *how many* | *why* |
| Data | counts and properties | session recordings, heatmaps, rage/dead clicks |
| Sampling | none — every event | recordings only, and only what is not blocked |
| Read a rate off it? | yes | **never** |

Both are fed from the same function, `track()` in `apps/web/lib/analytics.ts`, so
every storefront event exists in both places under the same name. What differs is
what each does with it.

---

## Setup

### 1. Create the project

1. Sign in at [clarity.microsoft.com](https://clarity.microsoft.com).
2. **Add new project** → name `Melting Moments Cakes`, site
   `meltingmomentscakes.com`, category `Food & Drink`.
3. **Settings → Overview** → copy the **project ID** (a ~10-character string).

### 2. Set the environment variable

| Where | Key | Value |
|---|---|---|
| Vercel, **web** project | `NEXT_PUBLIC_CLARITY_PROJECT_ID` | the project ID |

Public by design — it identifies the project and authorises nothing. There is no
secret half, and nothing to set on the API or in GitHub Actions.

Leave it empty and the storefront renders no script tag, makes no request and
sets no cookie. That is the intended off switch: verified by diffing the served
document with and without it.

Nothing else about the integration is configurable, deliberately, for the reason
written up in `docs/umami-analytics-setup.md` — an environment that disagrees
with the code about a *path* is a fault, not a deployment choice, and we have
been bitten by exactly that.

### 3. Set the masking mode

**Settings → Masking → Balanced** (the default). Leave it there. See
[Privacy](#privacy) below for what the code masks on top of it, and why the mode
alone is not enough.

### 4. That is the whole setup

There are no goals to create, no funnels to build and no events to declare.
Clarity discovers custom events as they arrive, and everything below appears on
its own once traffic starts.

---

## What the storefront sends

### Events

**All 66 of them**, by the same names as in Umami — `add_to_cart`,
`delivery_quote`, `phone_verify_failed`, and so on. The full list, with payloads
and firing sites, is in
[`docs/umami-analytics-setup.md`](umami-analytics-setup.md); it is not duplicated
here, because a second copy is a second thing to keep in sync and the first copy
is required by `CLAUDE.md` rule 10.

They are sent from inside `track()` rather than from the call sites, which is the
whole design: any event added to `analytics.ts` in future reaches Clarity with no
extra work and no chance of the two dashboards disagreeing about what happened.

They appear under **Filters → Events**, alongside Clarity's own no-code smart
events, and can be used to filter recordings and heatmaps.

### Tags (filters)

A Clarity tag is a filter you pick a value from before watching a session, so it
is useful in inverse proportion to how many distinct values it has. The payload
is therefore **allow-listed, not forwarded** (`TAG_KEYS` in
`apps/web/lib/clarity.ts`):

| Group | Keys |
|---|---|
| Where and what | `surface`, `list`, `creative`, `category`, `category_name`, `product_name`, `group_name`, `option_name`, `entry`, `sort`, `type`, `title`, `path`, `endpoint`, `channel` |
| Checkout shape | `step`, `field`, `fields`, `method`, `delivery_method`, `payment_provider`, `provider`, `stage`, `action`, `direction`, `code`, `promo_code`, `status` |
| Answers | `reason`, `serviceable`, `free_applied`, `free_available`, `in_stock`, `has_promo`, `has_results`, `has_modifiers`, `has_saved_address`, `has_pin`, `is_new`, `is_guest`, `personalised` |
| Join key | `order_number` |
| Derived | `value_band` |

Three rules decide what is on that list:

- **Money is banded, never passed.** `total: 143.75` is a filter with one value
  per order. `value_band` has six — `0`, `0-50`, `50-100`, `100-200`, `200-500`,
  `500+` — derived from `total ?? subtotal ?? value ?? price`, so "show me what
  the big baskets did" is a filter you can actually click.
- **`order_number` is passed despite being high cardinality.** It is the only
  join between a recording and a row in the admin, and *"show me the session
  behind order MM-20260810-0042"* is the question this shop will ask most.
- **Free text never goes.** Not `query` (whatever a customer typed into search),
  not `error_message` (a translated sentence), not an email, phone, address or
  coordinate. There is a test that fails if any of them starts to.

### Prioritised sessions

`clarity('upgrade', reason)` marks a session to survive sampling. Clarity keeps
100,000 recordings per project per day and this shop is nowhere near that, so
today it changes nothing — it is there so that the day it matters, the right
sessions are the ones kept.

The rule is a pattern, not a list, because this codebase's convention is that a
failure event says so in its name:

- anything matching `failed`, `error`, `unavailable`, `unserviceable`,
  `not_found` or `cancelled`;
- plus `order_completed`, `begin_checkout` and `view_checkout`.

A failure event added six months from now is prioritised without anyone
remembering to come back here. The upgrade reason is the event name itself, so
the **Recordings → filter by upgrade reason** list reads as a list of what went
wrong.

### Identity

`clarity('identify', user.id)` fires from `components/analytics/ClarityIdentity.tsx`
whenever a signed-in user is present, which is what stitches the three visits
before an order into one customer rather than three strangers.

The opaque user id and **nothing else**. Clarity's `identify` also takes a
`friendly-name` — where an email or a real name would go — and we deliberately
do not pass one. Guests are not identified at all: a guest id is minted per
checkout, so it would tie a session to an identity that means nothing and never
recurs.

To go from a Clarity recording to a customer, take the id and look it up in the
admin.

---

## Privacy

Umami stores nothing personal. Clarity records the DOM, so this needed real
thought rather than a default.

### What Clarity masks on its own

- **Input boxes and dropdowns, in every mode, not configurable.** Every text
  field on this site — password, email, phone, address lines, promo code,
  handwritten note — is covered by this and needs nothing from us.
- **Numbers and email addresses in page text**, in Balanced mode.

### What it does not, and what the code does about it

A delivery address rendered as *text* is not an input box, and a map with a pin
on somebody's home is not text at all. Those carry `data-clarity-mask="true"`,
which masks the node and its children regardless of the dashboard's mode:

| File | What is masked |
|---|---|
| `components/ui/LocationPicker.tsx` | the map — **the important one**: a map centred on a dropped pin *is* a home address, and no amount of text masking covers a picture |
| `app/[locale]/account/addresses/page.tsx` | each saved address card — name, street, phone |
| `app/[locale]/checkout/components/AddressModal.tsx` | the saved-address chooser |
| `app/[locale]/checkout/confirmation/page.tsx` | the "delivering to" line, and the thank-you sentence carrying the email |
| `app/[locale]/checkout/confirmation/CreateAccountNudge.tsx` | the email the account would be created under |
| `app/[locale]/account/settings/page.tsx` | the "reset link sent to …" confirmation |

In each case the surrounding chrome stays unmasked, so a recording still shows
*which* card was picked and *that* the step happened — the behaviour is intact,
the identity is not there.

Masking changes made in the dashboard take up to an hour to apply and are
**never retroactive**. The attributes in code apply from the next page load.

### Cookies and consent

Clarity sets first-party cookies (`_clck`, `_clsk`). Umami does not, and that
difference is worth knowing before anyone repeats "our analytics are
cookieless".

There is **no cookie banner on this site**, and cookie consent is left **off** in
the Clarity dashboard. That is a defensible position for a Sharjah bakery
serving the UAE: Clarity's consent enforcement covers EEA, UK and Swiss traffic.

If that changes — a banner is added, or EEA traffic starts to matter — the wiring
already exists. Turn on **Settings → Cookie consent**, and call
`clarity.consent(granted)` from the banner (`apps/web/lib/clarity.ts`). It sends
`consentv2` with both `ad_Storage` and `analytics_Storage`. Denied consent does
not stop Clarity: it runs in no-consent mode with a unique id per page view and
no cookies, so recordings continue and cross-page stitching stops.

---

## Reading it

### Heatmaps

**Heatmaps → enter a URL.** Click, scroll and area maps are generated from the
recorded sessions, so they need traffic on that exact path before they say
anything.

Worth knowing for this site specifically:

- **Paths are locale-prefixed.** `/en/all-products` and `/ar/all-products` are
  two separate heatmaps, and the Arabic one is RTL — a click map that looks
  mirrored is correct.
- **The catalogue pages are the ones to look at**, because that is where the
  conversion audit's open question lives: 71% of add-to-carts happen from a tile
  without a product page ever being opened, so the tile *is* the product page for
  most customers.
- **Filter a heatmap by an event** to answer a sharper question than the raw map
  does — the scroll map for sessions that fired `search_no_results` says
  something the sitewide scroll map cannot.

### Recordings worth watching first

| Question | Filter |
|---|---|
| Why do baskets die at the address step? | event `delivery_unserviceable` |
| What does a failed checkout look like? | event `checkout_error`, tag `field` |
| Is the coupon tray understood? | event `coupon_tray_shown` without `promo_applied` |
| What do big baskets do differently? | tag `value_band` = `200-500` or `500+` |
| The session behind this order | tag `order_number` |
| Did the phone gate cost us the sale? | event `phone_verify_failed` |

### Dashboard signals that are Clarity's own

`Rage clicks`, `dead clicks`, `excessive scrolling`, `quick backs` and
`script errors` are computed by Clarity with no instrumentation from us. Rage
clicks on a non-interactive element are the cheapest UX bug reports this shop
will ever get.

---

## Known limits, so they are not rediscovered as bugs

- **The tag is not proxied through this origin, unlike Umami.** Umami is served
  from `/vague/v.js` and posted to `/vague/api/send` precisely so blocklists have
  nothing to match. Clarity cannot be given the same treatment: the script
  hard-codes its own ingest hosts, so a first-party rewrite would serve the file
  and still beacon to `clarity.ms` — buying nothing while looking like it had.
  Blocklists carrying `||clarity.ms` therefore drop it. This shop's traffic is
  overwhelmingly mobile, so assume Clarity sees materially fewer sessions than
  Umami sees visits.
- **Which is why Umami stays the source of truth for counts.** A recording is a
  sample of a self-selected population. Never read a rate, a conversion or a
  total off Clarity; read *behaviour* off it and go back to Umami for the number.
- **`api_error` is loud in development.** With no API running, every page fires
  several — the global hook in `lib/api.ts` is doing its job. In production it is
  rare, and each one upgrades its session on purpose.
- **A tag with a value Clarity never received does not appear in the filter
  list.** An empty filter means "this never happened", not "this is broken" —
  the same trap `docs/umami-analytics-setup.md` records for goals.
- **Clarity must not be used on sites aimed at under-18s.** Microsoft's terms.
  Not a problem here, worth knowing before it is put on something else.
- **Values are truncated at 255 characters** before sending, and non-string,
  non-numeric, non-boolean values are dropped rather than stringified —
  Clarity silently ignores what it cannot read, and a filter that exists in code
  but not in the dashboard is the worst of both.

---

## Troubleshooting — "there are no recordings"

Work down this list; it is ordered by how often each has been the answer.

### 1. Is the tag on the page at all?

```bash
curl -s https://meltingmomentscakes.com/en | grep -o 'clarity.ms/tag/[^"]*'
```

Nothing means `NEXT_PUBLIC_CLARITY_PROJECT_ID` is not set on the Vercel **web**
project, or the deploy that set it has not shipped. Note that this is a check on
the *server-rendered* document — the script is `afterInteractive`, but the tag
is emitted by the server either way.

### 2. Does the browser load it?

Open the site, Network tab, filter `clarity`. Expect a request to
`https://www.clarity.ms/tag/<id>` and, once a session starts, uploads to a
regional `*.clarity.ms` host.

`(blocked:other)` or a request that never appears is a content blocker, which is
expected for some share of traffic and is the known limit above — not a fault.

### 3. Is our side reaching it?

In the console:

```js
typeof window.clarity          // "function" once the tag has loaded
window.clarity("event", "manual_check")
```

Then add to cart and watch for `event`/`set`/`upgrade` calls. If `window.clarity`
is a function but events are missing, the fault is in
`apps/web/lib/analytics.ts` or `lib/clarity.ts` and not in the dashboard — the
queue in `lib/deferred-dispatch.ts` waits 30 seconds for the tag and retries
after that, so "the script was slow" is not an explanation.

### 4. Is the CSP refusing it?

Console violations naming `clarity.ms` or `c.bing.com` mean the policy in
`apps/web/next.config.ts` has drifted. Both hosts must appear on `script-src`,
`connect-src` and `img-src` as appropriate. The policy is still
**report-only**, so a violation is a warning today and an outage the day it is
enforced.

### 5. Only then suspect the dashboard

Recordings take a few minutes to appear, and heatmaps need enough sessions on
the exact URL. A brand-new project with a handful of visits will legitimately
show nothing.

---

## Changelog

| Date | Change |
|---|---|
| 2026-08-10 | Initial integration. All 66 storefront events mirrored from `track()`; allow-listed tags with banded money and `order_number` as the join key; sessions upgraded on any failure event plus `order_completed`/`begin_checkout`/`view_checkout`; `identify` on the opaque user id only; `data-clarity-mask` on the six surfaces that render a customer's address, map pin or email as text; CSP extended for `*.clarity.ms` and `c.bing.com`. The queue that waits for a slow third-party script was extracted from `analytics.ts` to `lib/deferred-dispatch.ts` and is now shared by both trackers. |
