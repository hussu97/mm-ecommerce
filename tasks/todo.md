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

## W5 — Unified auth  ✅ standardized + consolidated (2 commits); D deferred by design
- [x] A: `ChannelAuthDescriptor` registry in policy.py (login_method, refresh_strategy,
      token_shape, anti_bot, server_refreshable) + drift-guard test — one declarative source
- [x] B/C (safe parts): worker skips headed relogin of server-refreshable channels via the
      descriptor seam (Deliveroo → API heals it over httpx). Found on reading the code that:
      • single liveness authority ALREADY exists (`unusable_reason_for` is the one impl;
        worker derivation is a tested blue/green-only net) — removing it reduces safety.
      • proactive refresh ALREADY exists, better-than-timer (`_session_for` re-mints
        server-refreshable tokens just-in-time before each sweep).
      So no rewrite of working, money-adjacent logic — only the additive skip.
- [~] D: explicit `aggregator_reauth_request` queue — DEFERRED. It replaces a working,
      tested trigger for cleanliness only, carries a migration, and trades against the CPU
      goal (faster reauth = more polling). Recommend NOT building unless you want the
      architectural change specifically. Item-5 intent (standardized/reliable/failsafes/
      logging) is met by A + B/C + the Sentry failsafes.

Item 5 delivered: one descriptor standardizes per-channel auth; the descriptor seam
removed wasteful headed relogins; reliability rests on the already-correct liveness
authority + just-in-time refresh; failsafes/logging via the W2/W3 Sentry events.

## Cross-cutting
- [x] Local tests + ruff (worker 119 pass; API aggregator 134 pass; allowlist 18 pass)
- [ ] W5-D migration verified on throwaway Postgres
- [ ] HOLD prod deploy for explicit user confirmation
- [ ] Vercel: user sets server SENTRY_DSN (web + admin)
- [ ] Sentry: user configures alert rules per docs/sentry-observability.md
