# Observability + CPU + Docs + Unified Auth

Branch: `feat/observability-cpu-auth-overhaul`
Plan: `~/.claude/plans/atomic-booping-hammock.md`

## W2/W3 — Sentry  ✅ DONE (3 commits)
- [x] Worker: sentry-sdk dep + `observability.py` init + Typer callback
- [x] Worker: SENTRY_DSN/SENTRY_ENVIRONMENT in compose worker block (W9 item 5) + enforce in test
- [x] Worker: captures — needs-human, reauth-failed, job-timeout, empty-capture
- [x] API: AsyncioIntegration; captures at RUN_FAILED + health; alerting.py
- [x] API: spawn_tracked() + report_result on orphan tasks
- [x] Web/admin: documented server SENTRY_DSN (Vercel action) + docs/sentry-observability.md alert rules

## W1 — CPU (code-only)  ✅ DONE (1 commit)
- [x] De-stack nightly: deliveroo finance 22→02, keeta finance 23→04
- [x] WORKER_JITTER_SECONDS (15m) on all fires
- [x] Heal poll 120 → 300
- [x] Background-only lean Chrome flags (WORKER_LEAN_CHROME), fingerprint untouched

## W4 — Ground-zero doc + artifact  ✅ DONE (1 commit + published artifact)
- [x] docs/integrators-and-aggregators.md (A–E, Mermaid, live counts)
- [x] Retire 7 old docs; keep aggregator-runbook; repoint all references
- [x] Visual HTML artifact published

## W5 — Unified auth rewrite  ⏳ Phase A DONE; B–D handed off
- [x] A: `ChannelAuthDescriptor` registry in policy.py (login_method, refresh_strategy,
      token_shape, anti_bot, server_refreshable) + drift-guard test — behaviour-preserving
- [ ] B: single liveness authority (drop worker `_channel_needs_reauth` fallback) +
      single backoff engine (delete reauth.py disk backoff → policy.next_backoff)
      ⚠ blue/green fallback + reauth-timing change — stage against instrumented prod
- [ ] C: `AuthProvider` interface + server-refresh-first + proactive pre-expiry refresh
      (start with deliveroo, the one REFRESH_SERVER_HTTPX channel)
- [ ] D: explicit `aggregator_reauth_request` queue (Alembic migration, ≤32-char id,
      verify on throwaway Postgres) replacing flag-and-poll + heal-poll churn

### Why B–D are staged, not rushed
They change money-adjacent session timing (relogin cadence, liveness verdict) and
carry a DB migration; the safe path is one shippable step at a time, validated with
the Sentry telemetry now in place (needs_human / reauth_failed / run_failed events)
and a staged blue/green deploy — not a big-bang cutover. The Phase-A descriptor is
the seam they all consume (`policy.server_refreshable`, `refresh_strategy`).

## Cross-cutting
- [x] Local tests + ruff (worker 119 pass; API aggregator 134 pass; allowlist 18 pass)
- [ ] W5-D migration verified on throwaway Postgres
- [ ] HOLD prod deploy for explicit user confirmation
- [ ] Vercel: user sets server SENTRY_DSN (web + admin)
- [ ] Sentry: user configures alert rules per docs/sentry-observability.md
