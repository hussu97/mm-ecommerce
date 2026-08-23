# Slider integration — done

Plan: `tasks/slider-integration.md`. Branch `claude/slider-integration-4b4113`,
worktree `.claude/worktrees/sms-verification-branding-bab18e`, off `main@4ee22ad`.

## Phase A — map re-split ("Cost-banded map v2")
- [x] New `BANDS` (22 zones from 15) + `REMAINDER_NAMES` in `scripts/build_delivery_zones.py`
- [x] Regenerated `uae_delivery_zones.geojson.json`, froze `.v4.`
- [x] `126_cost_banded_map_v2` — one transaction, fees carried through, batch groups restated
- [x] Fixed `create_version`: copies `batch_group_id`; the dead window loop is gone
      (`_windows_of` has taken a *group* id since 088 and was returning `[]`)
- [x] `LIVE_MIGRATION` + expectations in `tests/unit/test_delivery_zone_map.py`
- [x] `scripts/compare_delivery_maps.py` — 37 landmarks + a 98k-point grid sweep

## Phase B — Slider as a third courier
- [x] `providers/slider_provider.py` — User-Agent, non-JSON is an error, 4xx is not retried
- [x] `slider_service.py` — one pure `vehicle_for`, called by estimate and dispatch
- [x] `SLIDER` provider, `SliderStatusEnum`, status sets, rank
- [x] `courier_service.carrier_for`, `driver_assignment.Driver.from_slider`
- [x] `fulfilment_service._BOOKED_BY_US`; `127_slider_courier`
- [x] Admin TS + `packages/types` regenerated from OpenAPI

## Phase C — gated rollout
- [x] `trial_customer.py` recovered from `c47aab7^`, on `SLIDER_TRIAL_EMAILS`
- [x] Swap in `_dispatch_once`, and **in `batching_service.reserve`** — see below
- [x] Free-delivery waiver in `calculate_fee`, `quote_priced`, `compute_order_totals`
- [x] `test_trial_customer_free_delivery.py` recovered; the agreement test kept

## Phase D — webhooks + config
- [x] `POST /webhooks/slider` (token enforced) and `/webhooks/slider/staging` (inert)
- [x] Eleven `SLIDER_*` settings in all five locations

## Three departures from the plan, each to protect its own "no-op" guarantee

1. **`batching_service.reserve` asks `carrier_for` too.** The plan put the swap
   only in `_dispatch_once`, but `reserve` runs at confirmation and keys off the
   literal string `lalamove`. Left alone, every Dubai and Ajman order would have
   skipped its batch window the day the map named Slider and gone out alone at
   roughly three times the courier cost.
2. **The Slider zones keep their `batch_group_id`.** A grouped zone is promised
   its group's next window close; an ungrouped one is promised its courier's own
   answer. Detaching them would have changed what every customer in those zones
   is told while their orders carried on riding the run.
3. **`couriers.unbatched_promise_minutes` is 90, not the plan's 60.** The promise
   is read off the zone's courier while the gate hands all but one account back
   to noon Send, who promise 90. Sixty would have promised `Sharjah Core` an hour
   and delivered in an hour and a half. One admin edit when the gate comes off.

Pinned by `tests/unit/test_slider_rollout_is_a_no_op.py`.

## Rebased onto `main`, and what the QA pass then found

`main` gained zone alternates, courier reassignment and its own `create_version`
fix while this branch was in flight. Rebasing dropped my copy of that fix — theirs
landed first and goes further — renumbered the migrations to `126`/`127`/`128`,
and turned up six things the tests did not:

1. **The gate was writing `original_provider`.** That column means "a human
   moved this order", and three things read it that way — the admin's "moved
   from X" badge, `allowed_targets`, and `fulfilment_service._estimate`, which
   reads it as "written against a third-party zone" and answers tomorrow rather
   than an hour. It would have put a hand-moved badge and a next-day promise on
   nearly every Dubai order. Removed; the noon Send fallback never set it either.
2. **`_speed_of` asked the courier's name, not the schedule.** `Sharjah Core`
   would have dropped from "express" to "same day" the moment it was handed to
   Slider, for no reason a customer could see. It asks `is_batched` now.
3. **The checkout quoted the wrong vehicle.** A quote has a pin and no order, so
   it never learned the drop's emirate and priced the car everywhere — including
   inside Sharjah, where the booking uses the bike. The zone name is threaded
   through and `emirate_of` reads an emirate out of either shape.
4. **The fare cache was keyed on the pin alone**, so two quotes at one point in
   two zones would have shared one tier's price.
5. **The COD ceiling measured `order.total`**, not the cash the rider is actually
   handed — which is what goes into `cod_amount`.
6. **`fulfilment_reassignment` did not know Slider.** An admin would have been
   offered it as a target and got "Unknown courier 'slider'".

Two additions the audit argued for: `slider_service.refresh`, because Slider's
statuses only arrive by push and a lost one had no recovery path during a pilot
on production; and `courier_service.may_be_carried_by`, which states once that a
Slider zone's orders can ride a Lalamove run — otherwise
`assert_group_fits_polygon` and `update_polygon` would have detached six zones
from the runs that keep their promise honest.

## One fee moves, deliberately

Ras al-Khaimah beyond 78 km — Al Rams and the northern tip — was leftover at
80.00/200.00 third-party and is now `Ras al-Khaimah North` at 50.00/100.00 on
Lalamove. That is what the plan's routing table asks for (all eight measured RAK
areas go by Lalamove car) and it is a fee going **down**. Everything else on the
map is identical: verified over 98k grid points, nothing lost coverage.

---

## Bing Site Scan fixes (mm-scan-210826) — 2026-08-21

- [x] **ERROR Broken redirects** `/terms`, `/en/terms` — no `terms` route ever existed.
      The signup page linked to `/terms`, the proxy 307'd it to `/en/terms`, and that
      404'd. Decision: no terms page is wanted, so the link goes rather than the page
      arriving.
  - [x] Drop the `/terms` link from `app/[locale]/signup/page.tsx`; the sentence now
        names the privacy policy alone
  - [x] Delete `auth.tos_terms` / `auth.tos_and` from `scripts/seed_i18n.py` — the seed
        is the source of truth for UI strings and runs on every boot
  - [x] Migration `126_retire_terms_strings` to remove the rows the seed already wrote,
        guarded on the exact retired values
- [x] **ERROR Blocked by robots.txt** `/en/cart` — `Disallow` forbids the fetch, not the
      listing, and the basket is linked from the header of every page. A URL a crawler
      may not read is one it can only index as a bare link.
  - [x] `robots.ts` allows `/` with no disallow list
  - [x] `robots: { index: false, follow: false }` in a `layout.tsx` beside cart,
        checkout and account
  - [x] `account/layout.tsx` split into a server layout + `AccountShell` client
        component — metadata cannot be exported from `'use client'`
  - [x] `robots.test.ts` now asserts the disallow list is *empty*, with the reason
- [x] **WARNING Alt attribute missing** `/en` — `BannerPicture` hard-coded `alt=""` plus
      `aria-hidden`, covering every hero slide and promo band on the home page.
  - [x] `alt` is a required prop; `aria-hidden` follows it instead of pre-empting it
  - [x] `image_alt` on `HeroSlide` / `PromoItem`, falling back to headline / title
  - [x] `banner-alt.test.tsx` — asserted from rendered output, because `alt=""` is
        valid and nothing else in the toolchain objects to it
- [x] **WARNING Title too long** `/en/about` — 56-char CMS `seo.title` plus the 24-char
      `| Melting Moments Cakes` template = 80, and "Melting Moments" was in it twice.
  - [x] Migration `127_about_title_70`, guarded on the exact string `054` wrote.
        35 chars now, rendering at 59. Arabic was 69 and is untouched.
- [x] **NOTICE H1 missing** `/en/login` — `useSearchParams()` inside a fallback-less
      `<Suspense>` deferred the whole page to the client, so the served HTML was an
      empty shell.
  - [x] Server `page.tsx` owns the `<h1>` and the metadata; `LoginForm.tsx` is the
        client half and the only thing that needs the search params

### Review

Verified rather than assumed:

* **Migrations** — run to head against a throwaway Postgres (`initdb` + `pg_ctl` on
  :55433, UTF8/C). This caught a real bug: `to_jsonb(:new::text)` in `127` failed with
  a syntax error, because SQLAlchemy's `text()` will not bind a parameter followed by
  `::`. Now `CAST(:new AS text)`, matching migration `010`. Both guards were then
  exercised directly: an admin-edited about title and a hand-edited `tos_terms` row
  both survived a downgrade/upgrade cycle untouched, while the unedited rows went.
* **`next build`** — `/en/login` is prerendered as static HTML and the built
  `login.html` contains the `<h1>`; `cart.html`, `checkout.html` and `account.html`
  each carry `<meta name="robots" content="noindex, nofollow">`; `signup.html` has no
  `/terms` link left.
* **Tests** — 473 web unit tests pass (5 new), 2046 API tests pass. The banner-alt
  test was confirmed to fail when `alt` is reverted to `""`, so it is testing what it
  claims to. Two web test files would not load at first on an unresolvable
  `client-only` import; that was a stale `pnpm install` in the worktree, not the
  change — `client-only` is declared in `apps/web/package.json` and was simply not
  present. Installing fixed it and the pre-push hook went green.

Not done, deliberately: the `/terms` URL now 404s rather than redirecting, which is
correct — it never named a real page, and nothing links to it any more.

---

# VM resource audit remediation (2026-08-21)

An audit of the production VM found the disk at 59% (11 GB of 20 GB) with ~4 GB of
stale Docker images, and — far more seriously — **no offsite copy of the database at
all**: the GCS backup bucket named in `.env` had never been created, `gcloud` was not
on the deploy shell's `PATH` so the upload branch never ran, there was no backup cron,
and the disk had no snapshot schedule. Every dump lived on the same disk as the volume
it was backing up.

- [x] Create the `melting-moments-cakes-backups` GCS bucket + grant the VM's service account write access
- [x] Make `scripts/backup-db.sh` find `gcloud` regardless of login shell
- [x] Add a daily boot-disk snapshot schedule
- [x] Install the backup cron the deploy guide already documents
- [x] Cut the deploy image-prune window from 168h to 12h
- [x] Reclaim stale caches (docker images, build cache, apt archives, btmp, stray files)
- [x] Raise `max_connections` 20 -> 30 to remove the deploy-window overlap risk

## Review

**The audit's finding was not disk, it was that the shop had no offsite backup and
nobody could tell.** Three independent faults hid each other: the bucket named in
`.env` had never been created, `gcloud` was installed into a home directory and put
on `PATH` by `.bashrc` — which the non-interactive deploy shell never sources — so
the upload branch never ran, and the warning it printed instead went to a log nobody
reads. There was also no cron (backups happened only because `deploy.yml` takes one
before migrating) and no disk snapshot schedule. Every dump sat on the same 20GB
disk as the volume it was dumping.

Done and verified:

* **Bucket** `gs://melting-moments-cakes-backups`, me-central1, uniform access,
  public access prevention, 90-day lifecycle. The VM's default compute SA already
  holds `roles/editor`, so no service-account swap (and no instance stop) was needed.
* **`backup-db.sh` finds the SDK itself** across the usual install locations rather
  than trusting `PATH`. Proven under `env -i` with `PATH=/usr/bin:/bin` — i.e. the
  way cron will actually run it, not the way an interactive shell would.
* **The dump was restored, not just uploaded.** Pulled the GCS copy back down,
  restored into a throwaway database, and compared: orders 49/49, order_items 73/73,
  products 131/131, carts 3526/3526, audit_logs 207/207, and the same
  `alembic_version`. Throwaway dropped.
* **Snapshots**: `mm-backend-daily-snapshot`, 03:00 UTC, 14 days, plus one manual
  snapshot taken before any cleanup so there was a recovery point during the work.
* **Cron** installed under `hussainabbasi786110` (the deploy user and owner of
  `backups/`), with a logrotate rule for `/var/log/mm-backup.log`.
* **Prune window 168h -> 12h.** The 168h was commented as a rollback window and is
  not one: `rollback.yml` pulls the target SHA from GHCR and never reads the local
  cache. At a dozen-plus deploys a day it was only buying 59 dangling images.
* **Disk 59% -> 43%** (11G -> 8.0G): images, build cache, 336MB of apt archives,
  25MB of `btmp` (~25k failed SSH logins), a 27MB uncapped container log, and stray
  installers. Available RAM went 145MB -> 209MB.
* **Container logs capped** via a new `/etc/docker/daemon.json` (10m x 3), applied
  with `systemctl reload docker` so no container was stopped.
* **`max_connections` 20 -> 30.** 3 slots are superuser-reserved, so 17 were usable
  while two API apps can open 20 between them before deploy overlap is counted.

Two things worth carrying forward. First, a deploy runs `git reset --hard
origin/main`, so anything applied to the VM by hand is temporary — the fixes to
`backup-db.sh` and `max_connections` were both reverted by a deploy that landed
mid-session, which is precisely why they belong in this commit and not on the box.
Second, `docker-compose.prod.yml`'s postgres `command:` is a folded scalar: a `#`
line inside it becomes an argument to postgres rather than a comment. The rationale
comment now sits above the key, with a warning.

Not done, deliberately: giving the VM a dedicated service account with
`storage.objectCreator` (rather than the default account's project-wide `editor`)
would mean a compromised VM could add backups but not delete them. It needs an
instance stop, so it wants a maintenance window rather than a live afternoon.

---

## Local Slider webhook loop over a Cloudflare tunnel (2026-08-22)

Goal: place a real order against the local stack, have Slider's **sandbox**
carry it, and watch their status pushes land in the local DB.

- [x] Postgres + Redis up (`docker compose up -d postgres redis`); local DB at
      `130_about_title_70`, and the active polygon version already carries all
      six `slider` zones.
- [x] Local `apps/api/.env` given its own Slider block — `SLIDER_ENV=staging`
      (sandbox host), **fresh local-only** webhook tokens rather than the
      production ones, and `SLIDER_TRIAL_EMAILS=h_abbasi97@hotmail.com` so
      `courier_service.carrier_for` picks Slider for that signed-in account.
- [x] API on `127.0.0.1:8000`, web on 3000, admin on 3001.
- [x] `cloudflared tunnel --url http://localhost:8000` — a quick tunnel, so no
      Cloudflare login and no DNS record, at the cost of the hostname changing
      whenever it restarts.
- [x] Proved the inbound half end-to-end through the tunnel: a wrong token gets
      `{"received":true,"error":"unauthorised"}` and `signature_valid = f`; the
      right one is accepted, and both are journalled to `webhook_logs`.
- [x] Sandbox `SLIDER_API_KEY` / `SLIDER_ACCOUNT_ID` in. Proved with a live
      `/deliveries/fare`: 8.61 km, bike AED 14.28, car AED 17.24.
- [x] Webhook tokens back to the canonical pair — `0ddc50…` on the production
      route, `9b2910…` on the staging one. My first pass generated fresh
      local-only ones, which was wrong: the values were already configured on
      Slider's side. See `tasks/lessons.md`.
- [x] Test customer `slider-test@meltingmomentscakes.com` created locally and
      added to `SLIDER_TRIAL_EMAILS`. The seeded admin password no longer
      matches and Turnstile has a real secret locally, so `/auth/register` is
      not a route a script can take.
- [x] **Order MM-20260822-001 placed and carried by Slider.** 2× Classic
      Brownie, AED 70, COD, pinned at 25.33 / 55.38 (Sharjah Core). Delivery fee
      AED 0.00 — the trial half of `trial_customer` firing. Slider handle
      `96786296`, bike at AED 14.28, tracking on their staging site.
- [x] **Inbound loop proved against that order**, replayed through the tunnel:
      `rider_assigned` → driver recorded and order untouched; `picked_up` →
      `out_for_delivery`; the same `picked_up` again → `{"duplicate":true}`;
      a late `rider_assigned` → refused by rank, `courier_status` stayed
      `picked_up`. Exactly what `SLIDER_STATUS_RANK` is for.
- [ ] Configure the sandbox webhook in Slider's dashboard and watch a push that
      originates from them rather than from curl.

## Two dead clicks in the checkout (2026-08-23)

Both were taps that looked like they should do something and did nothing.

- [x] **"Verify your mobile number" landed on the wrong screen.** The prompt
      under the address — and the Place Order button when the gate turns it into
      "Verify your phone" — both opened the address sheet, which opens on the
      *list*. The list has no verification on it, so the only thing to tap was
      the address already chosen, which re-selects it and closes the sheet: three
      taps back to where they started. `AddressModal` now takes an `intent`, and
      `verifyPhone` opens straight on the form for the address the checkout is
      carrying, scrolls to the number, and rings it the way the checkout rings
      its own sections.
- [x] **The sheet self-closes once the number is proved**, when it was opened
      for that and the address was not touched — there is nothing left to save,
      and "Save and continue" would spend a round trip rewriting what is already
      there. Any edit keeps it open, because closing would discard the edit.
- [x] **Pressing Place Order when the SMS is the only thing missing** now opens
      that panel instead of ringing a link to it. Only when it is the only thing
      outstanding — a sheet over an unfilled email hides the other half.
- [x] **The single payment row read as unselected.** Card-on-delivery is the
      only option, and it was drawn grey-on-grey — pixel for pixel the
      *unselected* state of the delivery-method rows one section above — so
      people tapped it to select it. It now wears the chosen state and a tick,
      with an `sr-only` line saying so.
- [x] `AddressModal.test.tsx` covers both destinations, the scroll, the
      self-close, and the two cases that must not self-close. 481 tests pass.

### Follow-up: the send-code button was below the fold (2026-08-23)

- [x] The number is the **last** field on the address form — map, address, unit,
      label, two names, then the phone — so the verification panel under it sat
      below the fold of a scrolling sheet while the footer's "Save address"
      stayed pinned in view. What a customer saw under the number they had just
      typed was the save button, so that is what they pressed. The panel now
      lives *in the footer*, above that button, where it cannot be scrolled
      past; it names the number, since the field it was typed into may be behind
      the keyboard by then. Verified at 375×420 — a keyboard-height viewport.
- [x] Only one `PhoneVerify` is ever mounted: it renders Firebase's reCAPTCHA
      and a Turnstile widget into the DOM, so a second copy beside the field
      would be two bot checks racing for one send. The field keeps the green
      tick and nothing else.
- [x] No card at all on a build without the Firebase vars (preview deploys),
      where `PhoneVerify` renders nothing — otherwise it is a heading and a
      promise of a code over an empty space.
- [x] The scroll-to-the-number is instant and retried while the sheet settles.
      The map above it is a dynamic import that lays out afterwards, which left
      a single smooth scroll stranded 20px down with the customer looking at
      Dubai. Skipped once the field is in view, so it cannot fight a customer
      who scrolled themselves — and an unmeasurable layout counts as not-in-view
      rather than as visible.
- [x] Proved in the running app on a 375-wide viewport: promo `NEW` applied,
      address added, one tap on "Verify your mobile number" lands on the form
      scrolled to the number with SEND CODE above SAVE ADDRESS. The single
      payment row now reads as chosen — tinted, primary border, filled tick.
