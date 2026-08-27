# Aggregator auth: one human login, then autonomous refresh

## Goal
Stop treating IMAP OTP as the recovery path. A human signs in once (headed
browser). The **database** holds the live session (cookies, tokens, refresh
tokens, full Playwright `storage_state` + sessionStorage). The VM worker and
the httpx ingest hydrate from that row across deploys and restarts, and keep
the session alive by refreshing tokens / warming anti-bot cookies — not by
logging in again.

## Design
- **DB is the source of truth.** `aggregator_session.storage_state_encrypted`
  carries the Playwright blob (cookies with domain/path/expiry + localStorage)
  plus origin-scoped sessionStorage. Local `/data/sessions` is a cache the
  worker rewrites from the API on every start.
- **Hydrate on boot.** `warm-sessions` pulls decrypted sessions from
  `GET /aggregators/worker/sessions` (push-token auth) before opening a
  browser. A new image with an empty volume still resumes.
- **Interactive login** (`aggregator-bootstrap login --channel X`) is the only
  way to mint a session. UAE Chrome fingerprint (locale/TZ/geo/UA), same as
  Foodics.
- **Warm** reopens that state, re-runs the sensor, pushes the rotated cookies
  back. No OTP.
- **httpx ingest** keeps using the cookie/token replay; a req/s limiter plus
  one 429 retry so PerimeterX/Akamai are not burst.
- IMAP OTP stays in the tree unused on the default path.

## Checklist
- [x] Fingerprint module + wire into every Playwright context
- [x] Persist `storage_state` on `aggregator_session` (migration 153)
- [x] Worker hydrate endpoint + push of the full blob
- [x] Headed `login` CLI; demote OTP from `ensure_session`
- [x] Harvest refresh tokens / JWT expiry / sessionStorage
- [x] `AGGREGATOR_REQUESTS_PER_SECOND` in all five env places + limiter
- [x] Tests, OpenAPI regen
- [ ] Headed login with operator (capture live refresh grants after)
