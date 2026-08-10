# Microsoft Clarity on the storefront — sessions, heatmaps and every event

## Why

Umami answers *how many*. It cannot answer *why*. The conversion audit's open
questions are all of the second kind — why 71% of add-to-carts never reach the
basket, why `checkout_error` fires on `field: phone` more than anything else,
what a customer does in the fifteen seconds before `delivery_unserviceable`.
Clarity answers those with a recording and a heatmap, and it is free with no
volume cap that this shop will reach.

The point of the integration is not the tag. Pasting the tag takes one line and
gives you anonymous recordings you have no way to find. The work is making the
recordings **findable**: every one of the 66 storefront events reaching Clarity
as a filterable event, the dimensions that matter reaching it as tags, and the
sessions that went wrong being the ones Clarity keeps when it samples.

## Design

### One hook, not sixty-six call sites

`analytics.ts` already funnels every event through a single private `track()`.
Clarity is mirrored from inside that function, so all 66 events — and every
event added after this — reach both tools with no per-call-site work and no way
for the two dashboards to drift apart on what happened.

### The queue is the same problem twice

`analytics.ts` carries a poll-and-flush queue because the Umami script is
`afterInteractive` and anything tracked on mount races it — a real bug that lost
`order_completed` on slow connections. The Clarity tag has exactly the same
shape and exactly the same race. Rather than copy forty subtle lines and their
war story, the queue moves to `lib/deferred-dispatch.ts` and both use it. The
existing analytics tests are the proof that the extraction is behaviour-neutral.

### What becomes a tag, and what does not

Clarity tags are *filters over recordings*. A filter is only useful if it has
few enough values to pick from a list, so the payload is not forwarded wholesale:

- **Allow-listed keys only** — `surface`, `reason`, `step`, `method`, `list`,
  `creative`, … the low-cardinality dimensions you would actually filter on.
- **Money is banded, not passed.** `total: 143.75` is a filter with one value
  per order. `value_band: 100-200` is a filter you can use. One band tag,
  derived from `total ?? subtotal ?? value`.
- **`order_number` is passed**, high cardinality and all: it is the only join
  key between a recording and a row in the admin, and "show me the session
  behind this order" is the question the shop will ask most.
- **Free text never goes.** No `query`, no `error_message`, no `title` typed by
  a customer, no email, phone, address or coordinate.

### Which sessions survive sampling

Clarity keeps 100k recordings per project per day and samples above that. We are
nowhere near it, but `upgrade` costs nothing and the rule is worth encoding:
anything whose name reads as a failure (`*_failed`, `*_error`, `unavailable`,
`unserviceable`, `not_found`, `cancelled`) upgrades the session, as do
`order_completed`, `begin_checkout` and `view_checkout`. A pattern rather than a
list, so a failure event added next month is prioritised without anyone
remembering to add it here.

### Privacy is not the default here

Umami is cookieless and stores no personal data. Clarity records the DOM. Input
boxes and dropdowns are masked in every mode and email addresses and numbers are
masked in the default Balanced mode — but a delivery address rendered as *text*,
and a map with a pin on somebody's home, are neither. Those are masked
explicitly with `data-clarity-mask`, in the six places the storefront renders
them.

`identify` sends the user's opaque id and nothing else — never the email, never
the `friendly-name` argument.

## Tasks

- [x] `lib/deferred-dispatch.ts` — extract the wait-for-script queue; bound it
- [x] `lib/analytics.ts` — use it, and mirror every `track()` into Clarity
- [x] `lib/clarity.ts` — typed client API, tag allow-list, value bands, upgrade rule
- [x] `lib/clarity.test.ts` — 21 tests; extend `analytics.test.ts` for the mirror
- [x] `components/analytics/ClarityIdentity.tsx` — identify on sign-in, in `providers.tsx`
- [x] `app/[locale]/layout.tsx` — load the tag when the project id is set
- [x] `data-clarity-mask` on the address, map, contact and account surfaces
- [x] `next.config.ts` — CSP for `*.clarity.ms` and `c.bing.com`
- [x] `.env.example`, `PRODUCTION.md`, `README.md` — `NEXT_PUBLIC_CLARITY_PROJECT_ID`
- [x] `docs/microsoft-clarity-setup.md` — dashboard config, masking, troubleshooting
- [x] `docs/umami-analytics-setup.md` — changelog row (CLAUDE.md rule 10)
- [x] Verify: `pnpm --filter web test`, `lint`, `tsc`, and a real page load

## Review

### What shipped

| File | What |
|---|---|
| `lib/deferred-dispatch.ts` | new — the queue both trackers share, now bounded at 500 |
| `lib/clarity.ts` | new — `event` / `set` / `upgrade` / `identify` / `consentv2`, plus the mirror rules |
| `lib/clarity.test.ts` | new — 19 tests |
| `lib/analytics.ts` | `track()` mirrors to Clarity; queue logic moved out |
| `lib/analytics.test.ts` | +4 tests for the mirror, incl. "PII never becomes a tag" |
| `components/analytics/ClarityIdentity.tsx` | new — identify on sign-in |
| `app/providers.tsx` | mounts it inside `AuthProvider` |
| `app/[locale]/layout.tsx` | the tag, gated on `NEXT_PUBLIC_CLARITY_PROJECT_ID` |
| `next.config.ts` | CSP: `*.clarity.ms` on script/connect/img, `c.bing.com` on connect/img |
| 6 page/component files | `data-clarity-mask="true"` on address, map and account PII |
| `docs/microsoft-clarity-setup.md` | new — dashboard config, privacy, heatmap notes, troubleshooting |
| `docs/umami-analytics-setup.md` | changelog row + a pointer at the top (CLAUDE.md rule 10) |
| `.env.example`, `README.md`, `PRODUCTION.md` | the one new variable, documented in all three |

### Verification

- `pnpm vitest run` — **342 passed, 34 files**, against a stashed baseline of
  **319 / 33**. All 23 new tests are the Clarity ones; nothing existing changed
  behaviour, which is what makes the queue extraction safe.
- `tsc --noEmit` clean. `pnpm lint` reports 13 warnings and 0 errors, byte for
  byte the same as the stashed baseline.
- **Driven in a real browser** (Playwright/Chromium against `next dev`), which is
  what caught that the unit tests cannot prove the wiring reaches the components:
  - with the variable set, the document carries
    `<script src="https://www.clarity.ms/tag/…">` and the browser requests it;
  - a real storefront event travelled the whole path — `lib/api.ts` →
    `analytics.apiError` → `track()` → `mirrorToClarity` → `window.clarity` —
    arriving as `['event','api_error']`, `['set','status','500']`,
    `['set','endpoint','/cart']`, `['set','method','GET']`,
    `['upgrade','api_error']`. Tag allow-list, upgrade rule and ordering all
    confirmed live rather than inferred;
  - no CSP violation naming `clarity.ms` in the console.
- **The off switch was verified against a page that actually rendered.** The
  first attempt "proved" it on a server that was refusing connections — an empty
  grep on an empty response, which is exactly the both-hypotheses-predict-it
  observation in `tasks/lessons.md` (2026-08-06). Re-run properly: 56 KB served,
  the Umami tag present as a control, and zero occurrences of `clarity`.

### Known limits, recorded so they are not rediscovered as bugs

- **The Clarity tag is not proxied through this origin, unlike Umami.** Clarity's
  script hard-codes its own ingest hosts, so a first-party rewrite would serve
  the file and still beacon to `clarity.ms`. Blocklists that carry
  `||clarity.ms` therefore drop it, and mobile traffic behind a content blocker
  will be under-represented. Umami stays the source of truth for counts;
  Clarity is for *why*, on the sessions it does see.
- **A recording is a sample, a count is not.** Never read a rate off Clarity.
- **Masking changes take up to an hour** and are never retroactive.
- **Consent** is not wired to a banner because the site has none. The API is
  implemented (`clarity.consent()`); if EEA/UK traffic ever matters, turn cookie
  consent on in the dashboard and call it from the banner — the note is in
  `docs/microsoft-clarity-setup.md`.
</content>

---

# Firebase OTP: stop the SMS burn, and brand the message

## Why

A $4.04 charge was reported as phone verification. **It was not** — measured
afterwards, `SendVerificationCode` had run three times in twelve days. The charge
is the `mm-backend` e2-micro VM, its 20 GB pd-ssd and its static IP in Doha,
running since March on a billing account shared with three other projects.

The audit still found real defects, and they are worth closing on exposure alone.
The structural fault: `signInWithPhoneNumber` runs browser→Google directly, so
the step that costs money is the one step nothing of ours sits in front of.
Turnstile and the `10/minute` limiter guard `/auth/verify-phone`, which runs
*after* the SMS is already bought. `auth.py` says so itself: "Turnstile here
guards our ledger rather than Google's SMS bill."

## Tasks

- [x] `PhoneVerify`: ask `/auth/phone-verified` **before** sending. The account
      page already does this on the number; checkout never did, so a returning
      customer paid for a duplicate SMS.
- [x] `PhoneVerify`: 60s cooldown on resend, with the remaining seconds shown.
      The button had no throttle at all — every click was $0.09.
- [x] `PhoneVerify`: cap billed sends per mount, then stop offering the button.
- [x] `PhoneVerify`: require the Turnstile solution *before* the send, not only
      at the record step, so our bot check sits in front of the paid action.
- [x] Unit tests for all four.
- [x] Console steps for the operator (region policy, App Check, SMS Defense) and
      the custom auth domain that fixes the SMS text.

## Known limit of the code half

A determined bot calls Google's REST endpoint with the public API key and never
runs any of this. Client-side guards stop honest over-clicking and casual abuse,
which is what a $4.04 three-day bill actually looks like. Only App Check, the
AE-only region policy and reCAPTCHA SMS Defense are enforced by Google.

## Review

`PhoneVerify` now refuses to spend in four ways: it asks `/auth/phone-verified`
before buying anything, holds a 60s cooldown between sends, stops at three, and
will not send until Turnstile has answered. The cooldown is a **deadline**, not a
ticking counter — a counter is subject to background-tab timer throttling, and
this is precisely the panel a customer leaves to go and read an SMS, so a counter
would still owe them 40 seconds on return.

Two things surfaced while doing it. The already-verified check existed and was
wired up on the account page only, so checkout had been paying twice for the same
number. And `tsc` caught that a resend set `step` to `sending`, which fell out of
the code-entry branch and replaced the customer's half-typed code box with the
button they had just pressed; the branch now holds while a resend is in flight.

## Verified

`pnpm vitest run` — 34 files, 335 tests, all passing, 8 of them new and all
about how many times `signInWithPhoneNumber` may run. `tsc --noEmit` clean.
`pnpm lint` 0 errors (13 warnings, all pre-existing and elsewhere).

**Not verified:** the live Firebase round trip, for the same reason as before —
there are no Firebase env vars in this environment and `PhoneVerify` renders
`null` without them by design. The cooldown, the ceiling, the pre-send check and
the Turnstile gate are all proved against a mocked SDK; that the real SDK still
sends is unchanged code and needs a deploy that has the vars to confirm.

**Not fixed here — console-side, needs the account owner.** The code half only
stops honest over-clicking; anything driving Google's REST endpoint with the
public API key skips this file. Firebase App Check, the AE-only SMS region
policy, and reCAPTCHA SMS Defense are the parts Google enforces. The custom auth
domain that puts the brand in the SMS text is also console-side.
