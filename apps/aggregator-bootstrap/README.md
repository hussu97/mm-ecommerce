# aggregator-bootstrap

The browser side of the aggregator ingestion. The mm-ecommerce API pulls each
marketplace's data over `httpx`, but it cannot *log in* — Noon needs an email
OTP, Talabat and Noon sit behind PerimeterX/Akamai, and none publish a partner
API this shop is on. This worker is where the browser lives (Playwright), kept
deliberately out of `apps/api`, and deploys as its own job.

## Auth model

A human signs in **once**. Everything after that is a refresh, not a re-login.

1. **Login** (`aggregator-bootstrap login --channel noon`) — a real Google
   Chrome window (not Playwright, not Chrome-for-Testing). Cloudflare runs with
   no automation attached; you complete the check, OTP, captcha, or passkey.
   Once the portal session cookie exists, the worker connects over CDP, captures
   cookies / tokens / Playwright `storage_state` / sessionStorage, and writes
   them locally.
2. **Push** — that bundle is `POST`ed to `/api/v1/aggregators/session`. The API
   Fernet-seals it onto `aggregator_session`, including
   `storage_state_encrypted`. **The database is the source of truth.**
3. **Hydrate** — on every worker start (a deploy, a crash, a new VM image with
   an empty `/data` volume) `warm-sessions` `GET`s
   `/api/v1/aggregators/worker/sessions` with the push bearer and rewrites the
   local `storage_state` files. No login.
4. **Warm** — reopens the hydrated state, loads a probe page so PerimeterX /
   Akamai rotate `_px3` / `bm_sv`, and pushes the refreshed blob back. Still no
   login.
5. **Ingest** — the API's hourly httpx sweep replays the cookie/token fingerprint
   (TLS impersonation on Talabat/Noon), rate-limited to
   `AGGREGATOR_REQUESTS_PER_SECOND` (default 1).

If a warm lands on a login page the session is fully dead. The worker logs
`needs a headed login` and the API row flips to `needs_bootstrap`. It does
**not** poll IMAP for OTPs. Re-run `login --channel X`.

Keeta is special: its data is pulled *in-page* (requests are `mtgsig`-signed)
and pushed to `/aggregators/keeta/orders`. Hydrate still restores the browser
state so that in-page pull survives a restart.

## Commands

```
aggregator-bootstrap store-account --channel deliveroo --email you@x --password '…' --extra org_id=497912
aggregator-bootstrap mailbox-auth --channel talabat   # one-time Microsoft Graph connect
aggregator-bootstrap login --channel deliveroo --auto   # fill stored creds after Cloudflare
aggregator-bootstrap login --channel careem             # headed, you sign in
aggregator-bootstrap hydrate              # pull DB → local files
aggregator-bootstrap capture-and-push --all
aggregator-bootstrap warm-sessions        # hydrate + warm all (VM cron)
```

`store-account` writes the encrypted `aggregator_account` row (login method +
email/password + optional OTP mailbox). The same recipe is edited in the admin
**Logins** tab. Each OTP channel stores **its own** Microsoft app (client id +
secret) on that row — there is no global `EMAIL_MS_*` pair. After saving the
app, `mailbox-auth --channel X` signs that aggregator in once and stores a
refresh token. Passwords and secrets are Fernet-sealed by the API and never
returned on the admin health read. Deliveroo is `email_password` (no OTP), so
`--auto` can re-auth on a machine that can pass Cloudflare. OTP channels
(Talabat, Noon) need a connected Graph mailbox before the worker can pull the
code unattended. IMAP remains as a fallback on the same form.

## Configuration

`AGGREGATOR_API_URL`, `AGGREGATOR_SESSION_PUSH_TOKEN`, `STORAGE_STATE_DIR`,
`HEADLESS`. `STORAGE_STATE_DIR` defaults to `/data/sessions` — a cache. The
API row is what a new container hydrates from.

## Operations

A persistent volume at `/data` is still useful (survives an API blip), but it
is no longer load-bearing: a deploy that ships a fresh empty volume hydrates
from `aggregator_session` on the first `warm-sessions` tick.

The container runs as the non-root `pwuser`. Run it on a host with **stable
egress in the UAE region** — PerimeterX/Akamai bind cookies loosely to IP/ASN.
Do **not** run it on the small app VM: the browser's RAM is why it is a
separate job.

Default CMD is `warm-sessions` (hydrate + warm). First-time minting is
`login`, run headed from a laptop that can reach the API, once per channel.
