# aggregator-bootstrap

The browser side of the aggregator ingestion. The mm-ecommerce API pulls each
marketplace's data over `httpx`, but it cannot *log in* — Noon needs an email
OTP, Talabat and Noon sit behind PerimeterX/Akamai, and none publish a partner
API this shop is on. This worker is where the browser lives (Playwright), kept
deliberately out of `apps/api`, and deploys as its own job.

## What it does

1. **Capture** — opens a logged-in Playwright context (from a persisted
   `storage_state`), loads a channel's probe page, and reads the first real
   authenticated request it makes. That gives the cookies (including the
   load-bearing anti-bot cookie), the tokens, and the exact header fingerprint.
2. **Push** — `POST`s that bundle to `/api/v1/aggregators/session` with the
   shared bearer; the API seals it and the ingest loops replay it.
3. **Warm** — re-runs capture every few hours so the rotated anti-bot cookie
   (`_px3`, `bm_sv`) and short-lived tokens stay fresh **without** a re-login.
   Only when the stored session is fully stale is a full OTP login needed.

Keeta is special: its data is pulled *in-page* (its requests are signed in the
page) and pushed to `/aggregators/keeta/orders`. That in-page pull is the one
piece still to be ported from the standalone scraper.

## Commands

```
aggregator-bootstrap capture-and-push --channel careem
aggregator-bootstrap capture-and-push --all
aggregator-bootstrap warm-sessions            # all channels
```

## Configuration

`AGGREGATOR_API_URL`, `AGGREGATOR_SESSION_PUSH_TOKEN`, `STORAGE_STATE_DIR`,
`HEADLESS`, and the `OTP_IMAP_*` mailbox settings (see `config.py`).

## Where it runs

On a host with a **stable egress IP in the UAE region** — PerimeterX/Akamai bind
their cookies loosely to IP/ASN, so the worker, the warmer and the API's ingest
should egress consistently, or the captured anti-bot cookie may be rejected. Do
**not** run it on the small app VM: the browser's RAM is the whole reason it is a
separate job.

## Status

Framework, generic capture (all five channels via `channels/probes.py`), push,
warm, and the CLI are complete and unit-tested. Still to port from
`mm-aggregator-automation`: the automated OTP **login** flows (for re-login when
a `storage_state` goes fully stale) and Keeta's in-page order pull. Until those
land, a session is established by a one-time manual login into the profile the
`storage_state` is captured from.
