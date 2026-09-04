# Sentry Observability & Alerting

How every service reports to Sentry, and the alert rules to configure. Written
2026-09 after the worker was found to have **zero** Sentry and the API's
partial-failures were logged below the capture threshold.

## Services and how each reports

| Service | Process | DSN source | Init | Notes |
|---|---|---|---|---|
| **api** | FastAPI (`app/main.py`) | compose `SENTRY_DSN` (api-environment anchor) | `app_setup.configure_observability` | `service=api`; FastAPI + SQLAlchemy + Asyncio integrations |
| **pos-api** | FastAPI (`app/pos_main.py`) | compose `SENTRY_DSN` (pos-environment anchor) | same | `service=pos-api` |
| **aggregator-worker** | Python daemon (`aggregator-bootstrap serve`) | compose `SENTRY_DSN` (worker `environment:` block) | `observability.init_sentry` via Typer callback | `service=aggregator-worker`; **was previously a blind spot** |
| **web** (storefront) | Next.js on Vercel | **Vercel env `SENTRY_DSN`** (server) + `NEXT_PUBLIC_SENTRY_DSN` (browser) | `sentry.server/edge.config.ts` + `onRequestError` | Set the **server** DSN in Vercel or there are no backend logs |
| **admin** | Next.js on Vercel | same as web | same | Sentry project `mm-admin` |

### ⚠️ Action required (web/admin backend logs)
The Next.js apps deploy on **Vercel**, not via docker-compose, so the compose
DSN never reaches them. Server-side capture (RSC render, route handlers, server
actions — wired through `instrumentation.ts`'s `onRequestError`) only fires when
the **server** config has a DSN at runtime. Set **`SENTRY_DSN`** (and optionally
`SENTRY_ENVIRONMENT=production`) in each Vercel project's environment variables.
Until then, "no backend Sentry logs for the website/admin" is expected.

## Alert rules to configure (Sentry UI)

All aggregator events carry a `channel` tag and an `aggregator_issue` tag, and a
stable fingerprint so each condition is one grouped issue. Build alert rules on
the `aggregator_issue` tag:

| `aggregator_issue` | Meaning | Where raised | Recommended alert |
|---|---|---|---|
| **`needs_human`** | Automated re-login cannot recover a channel (captcha/passkey/mailbox) — a person must run `login --channel X` on the VM | worker `reauth.py` | **Notify immediately** (this is the one that pages) |
| `reauth_failed` | A reauth attempt failed and armed a backoff | worker `reauth.py` | Digest / notify on N in a window |
| `empty_capture` | A job ran but captured nothing (`keeta`/`deliveroo` orders/menu/invoices) | worker `warm.py` | Digest; investigate if sustained |
| `job_timeout` | A headed job exceeded its budget and Chrome was killed | worker `daemon.py` | Digest |
| `run_failed` | A channel's nightly/rolling sync run failed (session not live) | API `ingest.py` | Notify if it persists across the night |
| `session_unhealthy` | A session is not live or has gone stale | API `ingest.py` `_log_health` | Digest |

Background-task deaths are tagged `task=<name>` (e.g. `aggregator_ingest`,
`batch_scheduler`) via `app/core/background.report_result`.

## Design notes
- **DSN-guarded, never-raising**: every capture helper (`observability.py` in the
  worker, `app/core/alerting.py` in the API) is a no-op without a DSN and never
  throws — telemetry must not break a scraper or a request.
- **Fingerprints over spam**: the worker's reauth backoff already debounces, so
  `needs_human` is one grouped issue, not per-tick noise.
- **W9**: the worker `SENTRY_DSN` is enforced in
  `apps/api/tests/unit/test_compose_env_allowlist.py` (its absence from the
  compose allow-list is exactly the silent-failure class W9 exists to prevent).
